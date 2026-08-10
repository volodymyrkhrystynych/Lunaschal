"""Chat-attachment storage helpers.

Importing `backend.imaging` registers Pillow's HEIF opener, so a photo taken on
an iPhone can be transcoded to JPEG on upload — which is what the vision model
in `backend/ai/images.py` needs, since it refuses HEIC outright.
"""
import backend.imaging  # noqa: F401
