"""Rendered resume files, one directory per application.

`<root>/<application_id>/<resume_version_id>.{pdf,docx}` — a version-scoped
filename rather than a fixed one, so retention can delete a single superseded
render without touching the version that was actually sent.
"""
from pathlib import Path

from backend.storage import IdScopedStorage, is_safe_name

_storage = IdScopedStorage('JOBS_ROOT', './data/jobs')

jobs_root = _storage.root
application_dir = _storage.dir
delete_application_dir = _storage.delete_dir
resolve_stored_path = _storage.resolve_stored_path


def resume_path(application_id: str, version_id: str, ext: str) -> Path | None:
    """Where a rendered resume lives. None if either id is unsafe."""
    if ext not in ('pdf', 'docx'):
        return None
    d = application_dir(application_id)
    if d is None or not is_safe_name(version_id):
        return None
    return d / f'{version_id}.{ext}'


def fill_run_screenshot_path(application_id: str, run_id: str) -> Path | None:
    d = application_dir(application_id)
    if d is None or not is_safe_name(run_id):
        return None
    return d / f'fill-run-{run_id}.png'
