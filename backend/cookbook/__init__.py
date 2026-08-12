"""Recipe storage helpers.

See backend/food/__init__.py — importing backend.imaging here registers
Pillow's HEIF opener, needed before recipe photo uploads can be transcoded.
"""
import backend.imaging  # noqa: F401
