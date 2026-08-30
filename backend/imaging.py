"""Image re-encoding shared by the features that accept photo uploads.

HEIC is what an iPhone actually produces, and almost nothing else reads it —
browsers won't render it. Transcoding at the upload boundary is what keeps that
one consumer's problem from becoming every consumer's. (`backend/ai/images.py`
no longer needs it: it re-encodes every image on the way to the model anyway,
and imports this module for the HEIF opener registered below.)

This lived in `backend/routes/food.py` until chat attachments needed the same
guarantee.
"""
import logging
import os
from pathlib import Path

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


def rotate_clockwise(path: Path | str) -> int:
    """Rotate a stored image 90 degrees clockwise, in place.

    The write goes through a sibling temporary file so a failed encoder never
    leaves the journal attachment half-written. EXIF orientation is baked in
    first; otherwise a portrait phone photo can receive two rotations when a
    browser applies the old orientation tag to the newly rotated pixels.

    Returns the new file size. Raises Pillow/OSError errors for the route to
    turn into a useful 422 without changing the original.
    """
    from PIL import Image, ImageOps

    path = Path(path)
    temp = path.with_name(f'.{path.name}.rotating')
    try:
        with Image.open(path) as source:
            image_format = source.format
            image = ImageOps.exif_transpose(source)
            rotated = image.transpose(Image.Transpose.ROTATE_270)

            save_kwargs = {}
            exif = rotated.getexif()
            exif.pop(274, None)  # Orientation: the transform is now in pixels.
            if exif:
                save_kwargs['exif'] = exif.tobytes()
            if image_format == 'JPEG':
                save_kwargs.update(quality=95, optimize=True)

            rotated.save(temp, format=image_format, **save_kwargs)
        os.replace(temp, path)
        return path.stat().st_size
    finally:
        temp.unlink(missing_ok=True)


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
