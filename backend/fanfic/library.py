"""Library bookkeeping shared by the web routes and the phone's sync operations.

Shelving, read marks and folder filing each have two callers -- a REST route
and a replayed mobile operation -- and they must agree on the rules, so the
rules live here. Callers own the transaction: nothing in this module commits.
"""
import time

SHELVES = ('to_read', 'reading', 'paused', 'finished', 'dropped')


def check_rating(rating) -> None:
    if rating is not None and (not isinstance(rating, int) or isinstance(rating, bool)
                               or not 1 <= rating <= 5):
        raise ValueError('rating must be null or an integer from 1 to 5')


def check_review(review) -> None:
    if review is not None and not isinstance(review, str):
        raise ValueError('review must be null or a string')


def check_shelf(shelf) -> None:
    if shelf not in SHELVES:
        raise ValueError(f"shelf must be one of {', '.join(SHELVES)}")


def clean_review(review):
    return review.strip() or None if review else None


def set_shelf(db, fic_id: str, shelf: str) -> bool:
    """Move a fic to a shelf. `shelved_at` only moves when the shelf does, so
    re-saving the same shelf (a replayed edit, a review saved alongside it)
    keeps the date it was really put there."""
    now = int(time.time())
    cur = db.execute(
        'UPDATE fics SET shelved_at=CASE WHEN shelf IS ? THEN shelved_at ELSE ? END,'
        ' shelf=?, updated_at=? WHERE id=?',
        (shelf, now, shelf, now, fic_id))
    return cur.rowcount > 0


def set_chapters_read(db, fic_id: str, chapter_ids: list[str], read: bool) -> bool:
    """Mark chapters read or unread. False when any id isn't this fic's."""
    if not chapter_ids:
        return True
    placeholders = ','.join('?' * len(chapter_ids))
    owned = {r[0] for r in db.execute(
        f'SELECT id FROM fic_chapters WHERE fic_id=? AND id IN ({placeholders})',
        (fic_id, *chapter_ids))}
    if owned != set(chapter_ids):
        return False
    if read:
        now = int(time.time())
        db.executemany(
            'INSERT OR IGNORE INTO fic_chapter_reads(chapter_id, fic_id, created_at)'
            ' VALUES (?,?,?)',
            [(cid, fic_id, now) for cid in chapter_ids])
    else:
        db.execute(
            f'DELETE FROM fic_chapter_reads WHERE fic_id=? AND chapter_id IN ({placeholders})',
            (fic_id, *chapter_ids))
    return True


def file_in_folder(db, fic_id: str, folder_id: str) -> None:
    # Filing by hand claims the membership: an upgrade from 'import' is what
    # stops the personal-tag sync from later unfiling something the user
    # deliberately put here.
    db.execute(
        'INSERT INTO fic_folder_items(folder_id, fic_id, origin, created_at)'
        " VALUES (?,?,'manual',?) ON CONFLICT(folder_id, fic_id)"
        " DO UPDATE SET origin='manual'",
        (folder_id, fic_id, int(time.time())))


def set_folders(db, fic_id: str, folder_ids: list[str]) -> bool:
    """Make `folder_ids` exactly the folders this fic is in, as the phone's
    folder checklist shows them. False when a folder doesn't exist.

    Unticking removes any membership, imported ones included -- the same as
    the web's remove button, and the next bookmark sync may file it again,
    which is what Deleting an imported folder already warns about. Ticking
    claims the membership as manual."""
    wanted = set(folder_ids)
    if wanted:
        placeholders = ','.join('?' * len(wanted))
        found = {r[0] for r in db.execute(
            f'SELECT id FROM fic_folders WHERE id IN ({placeholders})', tuple(wanted))}
        if found != wanted:
            return False
    current = {r[0] for r in db.execute(
        'SELECT folder_id FROM fic_folder_items WHERE fic_id=?', (fic_id,))}
    for folder_id in current - wanted:
        db.execute('DELETE FROM fic_folder_items WHERE folder_id=? AND fic_id=?',
                   (folder_id, fic_id))
    for folder_id in sorted(wanted):
        file_in_folder(db, fic_id, folder_id)
    return True
