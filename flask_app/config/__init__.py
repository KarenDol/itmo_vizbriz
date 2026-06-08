"""Configuration package exports and legacy compatibility helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path


__all__ = ["Config"]


def _load_legacy_config_class():
    """Load the legacy ``Config`` class from ``flask_app/config.py`` safely."""

    module_path = Path(__file__).resolve().parent.parent / "config.py"
    spec = importlib.util.spec_from_file_location(
        "flask_app._legacy_config_module",
        module_path,
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive fallback
        raise ImportError("Unable to locate legacy config module")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Config


Config = _load_legacy_config_class()
