"""The user's own tags on a source site, mirrored into library folders.

Two sites carry a personal filing the library would otherwise have to be
given by hand: XenForo bookmark labels (/account/bookmarks) and AO3 bookmark
tags (/users/<name>/bookmarks). Both land here, so there is exactly one set
of rules for what a tag does to a folder.

The site's *public* tags are not this — those are fic_site_tags, shown as tag
pills, and folding them in would bury the folder bar under fandom names.
"""

import re
import time
from typing import Iterable

from ulid import ULID

MAX_NAME_LENGTH = 100


def normalize(names: Iterable[str]) -> list[str]:
    """Trim, collapse internal whitespace, drop empties, cap length and
    dedupe case-insensitively, keeping the site's own order."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in names:
        if not isinstance(raw, str):
            continue
        name = re.sub(r'\s+', ' ', raw).strip()[:MAX_NAME_LENGTH].strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(name)
    return out


def _folder_id(db, name: str) -> str:
    """The folder for a tag name, created if there isn't one.

    Matching is case-insensitive so "Worm" from AO3 and "worm" from a forum
    are one folder rather than twins; an existing folder is reused whatever
    its origin, and keeps it — a folder the user made by hand does not become
    an imported one just because a tag happens to share its name.
    """
    row = db.execute('SELECT id FROM fic_folders WHERE name = ? COLLATE NOCASE',
                     (name,)).fetchone()
    if row:
        return row['id']
    folder_id, now = str(ULID()), int(time.time())
    db.execute(
        'INSERT INTO fic_folders(id, name, position, origin, created_at, updated_at)'
        " VALUES (?,?,(SELECT COALESCE(MAX(position),-1)+1 FROM fic_folders),'import',?,?)",
        (folder_id, name, now, now))
    return folder_id


def sync_personal_folders(db, fic_id: str, names: Iterable[str]) -> list[str]:
    """File `fic_id` into a folder per personal tag, and out of the imported
    folders whose tag is gone. Returns the folder ids it filed the fic into.

    An empty tag list is a no-op rather than a clear-out: a bookmark with no
    labels and a parse that found none look identical from here, and only one
    of those should empty a fic's folders. Removing the last label on a site
    therefore leaves the last folder — cheap to undo by hand, unlike a
    library that quietly unfiled itself after a markup change.
    """
    names = normalize(names)
    if not names:
        return []
    now = int(time.time())
    folder_ids = [_folder_id(db, name) for name in names]
    for folder_id in folder_ids:
        # Manual wins: an existing row is left exactly as the user filed it,
        # so a hand-filed membership is never downgraded to one the sync may
        # later delete.
        db.execute(
            'INSERT INTO fic_folder_items(folder_id, fic_id, origin, created_at)'
            " VALUES (?,?,'import',?) ON CONFLICT(folder_id, fic_id) DO NOTHING",
            (folder_id, fic_id, now))
    placeholders = ','.join('?' * len(folder_ids))
    db.execute(
        f"DELETE FROM fic_folder_items WHERE fic_id=? AND origin='import'"
        f' AND folder_id NOT IN ({placeholders})',
        (fic_id, *folder_ids))
    db.commit()
    return folder_ids
