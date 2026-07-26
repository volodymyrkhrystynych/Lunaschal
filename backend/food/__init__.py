"""Food-log storage helpers.

Registering the HEIF opener here (once, at import) lets Pillow open the HEIC/HEIF
photos iPhones produce, so their EXIF (capture date + GPS) is readable and they
can be transcoded to JPEG. Optional — degrades gracefully if pillow-heif isn't
installed.
"""
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # pragma: no cover - optional dependency
    pass
