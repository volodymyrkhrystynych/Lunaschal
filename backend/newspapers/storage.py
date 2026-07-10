import os
import re
from pathlib import Path

PAPERS = {
    'toronto-star': {'label': 'Toronto Star', 'url': 'https://www.frontpages.com/toronto-star/'},
    'nyt': {'label': 'The New York Times', 'url': 'https://www.frontpages.com/the-new-york-times/'},
}

# frontpages.com serves covers as webp; jpg/png kept as a fallback in case
# that ever changes.
EXT_FROM_CONTENT_TYPE = {
    'image/webp': 'webp',
    'image/jpeg': 'jpg',
    'image/png': 'png',
}
DEFAULT_EXT = 'jpg'

_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def newspapers_root() -> Path:
    return Path(os.environ.get('NEWSPAPERS_ROOT', './data/newspapers')).expanduser().resolve()


def build_path(paper: str, date: str, content_type: str) -> Path:
    ext = EXT_FROM_CONTENT_TYPE.get(content_type, DEFAULT_EXT)
    return newspapers_root() / paper / f'{date}.{ext}'


def resolve_stored_path(path_str: str) -> Path | None:
    """Only serve a path that's still a direct grandchild of the newspapers
    root (root/<paper>/<file>) — cheap defense in depth around the stored
    DB value, even though it's never built from request input."""
    path = Path(path_str)
    if path.parent.parent != newspapers_root():
        return None
    return path
