"""Image re-encoding shared by the features that accept photo uploads.

HEIC is what an iPhone actually produces, and almost nothing else reads it:
browsers won't render it, and `backend/ai/images.py` refuses to send one to the
vision model (its `_EXT_MIME` deliberately omits heic/heif, because llama.cpp's
projector would receive garbage bytes). Transcoding at the upload boundary is
what keeps both of those true downstream instead of every consumer having to
know about it.

This lived in `backend/routes/food.py` until chat attachments needed the same
guarantee.
"""
import logging

logger = logging.getLogger(__name__)

# Registering the HEIF opener once, at import, is what lets Pillow open the
# HEIC/HEIF an iPhone produces at all — both to transcode it and to read its
# EXIF. Optional: everything below degrades to "unsupported upload" without it.
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # pragma: no cover - optional dependency
    pass

# Extensions that need transcoding before anything else in the app will read them.
HEIC_EXTS = {'heic', 'heif'}


def transcode_to_jpeg(file, path) -> bool:
    """Re-encode a HEIC/HEIF upload to JPEG at `path`, baking in EXIF orientation
    but preserving the rest of the EXIF (date, GPS). Returns False on failure.

    The EXIF has to survive: the food log treats a photo's capture date and GPS
    as the source of truth for when and where the meal happened.
    """
    try:
        from PIL import Image, ImageOps
        img = ImageOps.exif_transpose(Image.open(file.stream))
        exif_bytes = img.info.get('exif')
        kwargs = {'quality': 90}
        if exif_bytes:
            kwargs['exif'] = exif_bytes
        img.convert('RGB').save(path, 'JPEG', **kwargs)
        return True
    except Exception as e:
        logger.warning('HEIC transcode failed: %s', e)
        return False
