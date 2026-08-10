"""Food-log storage helpers.

Importing `backend.imaging` here registers Pillow's HEIF opener, so the HEIC/HEIF
photos iPhones produce can be opened at all — both to read their EXIF (capture
date + GPS) and to transcode them to JPEG. That registration moved to
`backend/imaging.py` when chat attachments needed the same guarantee; this import
is what keeps it happening for any code path that reaches food storage first.
"""
import backend.imaging  # noqa: F401
