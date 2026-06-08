"""
Main blueprint route groups.

This package contains "route group" modules that register endpoints onto the
existing `main` Blueprint defined in `flask_app/routes/main_routes.py`.

Keeping registration separate allows `main_routes.py` to shrink progressively
without changing the public Blueprint name or endpoint names.
"""

