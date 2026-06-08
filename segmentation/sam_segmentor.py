"""
SAM-based slice segmentation helper.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

try:
    import torch
    from segment_anything import SamPredictor, sam_model_registry
except ImportError as exc:  # pragma: no cover - dependency missing
    SamPredictor = None  # type: ignore
    sam_model_registry = {}  # type: ignore
    logger.warning("segment_anything not available: %s", exc)


class SAMSegmentor:
    """
    Thin wrapper around Meta's Segment Anything predictor.
    Loads weights once and exposes segment_from_box(image, bbox) -> mask.
    """

    def __init__(
        self,
        checkpoint_path: Optional[str] = None,
        model_type: Optional[str] = None,
        device: Optional[str] = None,
    ):
        if SamPredictor is None or not sam_model_registry:
            raise RuntimeError(
                "segment_anything package not installed. "
                "Install it or set ANNOTATOR_USE_SAM=0 to disable SAM inference."
            )

        checkpoint_path = checkpoint_path or os.getenv(
            "SAM_WEIGHTS_PATH",
            "/home/ec2-user/sam_weights/sam_vit_h.pth",
        )
        model_type = model_type or os.getenv("SAM_MODEL_TYPE", "vit_h")

        ckpt = Path(checkpoint_path)
        if not ckpt.exists():
            raise FileNotFoundError(
                f"SAM checkpoint not found at {ckpt}. "
                "Set SAM_WEIGHTS_PATH to the downloaded .pth file."
            )

        if device is None:
            if torch.cuda.is_available():
                device = "cuda"
            elif torch.backends.mps.is_available():  # type: ignore[attr-defined]
                device = "mps"
            else:
                device = "cpu"

        logger.info("Loading SAM model (%s) from %s on device %s", model_type, ckpt, device)
        try:
            sam_model = sam_model_registry[model_type](checkpoint=str(ckpt))
            sam_model.to(device)
            self.predictor = SamPredictor(sam_model)
            self.device = device
            logger.info("SAM model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load SAM model: {e}", exc_info=True)
            # Try CPU fallback if CUDA failed
            if device == "cuda":
                logger.warning("CUDA loading failed, attempting CPU fallback...")
                try:
                    device = "cpu"
                    sam_model = sam_model_registry[model_type](checkpoint=str(ckpt))
                    sam_model.to(device)
                    self.predictor = SamPredictor(sam_model)
                    self.device = device
                    logger.info("SAM model loaded on CPU as fallback")
                except Exception as cpu_error:
                    logger.error(f"CPU fallback also failed: {cpu_error}", exc_info=True)
                    raise RuntimeError(f"SAM model loading failed on both CUDA and CPU: {e}") from cpu_error
            else:
                raise RuntimeError(f"SAM model loading failed: {e}") from e

    def segment_from_box(
        self,
        image_rgb: np.ndarray,
        bbox: Tuple[int, int, int, int],
        multimask_output: bool = False,
    ) -> np.ndarray:
        """
        Args:
            image_rgb: uint8 numpy array shape [H,W,3]
            bbox: (x_min, y_min, x_max, y_max)
        Returns:
            Binary mask uint8 array shape [H,W]; 255 where foreground, 0 elsewhere.
        """
        if image_rgb.ndim != 3 or image_rgb.shape[2] != 3:
            raise ValueError(f"Expected RGB image (H,W,3), got {image_rgb.shape}")

        self.predictor.set_image(image_rgb)
        input_box = np.array([bbox], dtype=np.float32)
        kwargs = {"multimask_output": multimask_output}
        try:
            masks, scores, _ = self.predictor.predict(
                boxes=input_box,
                **kwargs,
            )
        except TypeError:
            # Older/newer segment-anything releases use singular "box" arg
            masks, scores, _ = self.predictor.predict(
                box=input_box[0],
                **kwargs,
            )
        chosen = masks[0]
        binary = (chosen.astype(np.uint8) * 255)
        logger.debug(
            "SAM scores=%s bbox=%s foreground_pixels=%s",
            scores.tolist(),
            bbox,
            int(binary.sum() / 255),
        )
        return binary


_sam_segmentor: Optional[SAMSegmentor] = None


def get_sam_segmentor() -> SAMSegmentor:
    global _sam_segmentor
    if _sam_segmentor is None:
        _sam_segmentor = SAMSegmentor()
    return _sam_segmentor

