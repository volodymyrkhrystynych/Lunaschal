"""Where a journal entry was made, for one that arrived without saying.

The composer attaches a device fix when it has a recent one. Plenty of entries
arrive without: a Watch recording (the Watch has no location of its own here),
a standalone Record/Transcribe, a screenshot, an entry saved after the
Capture tab's fix went stale, anything typed on the desktop. The phone records
a location trail all day, and the phone, Watch and iPad travel together, so
the trail at the entry's capture moment is where it was made.

Such coordinates are stored like a device fix but labelled
`location_source='trail'`, so whatever reads them can tell a measured fix
from a looked-up one. **An entry's own fix is never replaced**, and an entry
the trail has nothing for (more than lookup.MAX_GAP_S from any fix, no stay
spanning it) stays unlocated rather than guessed.
"""
from backend.location.lookup import MAX_GAP_S, fix_near


def fill(db, entry_id: str) -> bool:
    """Locate one entry from the trail if it has no coordinates. Does not
    commit; returns whether it set them."""
    row = db.execute('SELECT latitude, longitude, created_at FROM journal_entries WHERE id=?',
                     (entry_id,)).fetchone()
    if row is None or (row['latitude'] is not None and row['longitude'] is not None):
        return False
    found = fix_near(db, row['created_at'])
    if found is None:
        return False
    # Guarded again in SQL: a create racing a trail upload must not have its
    # device fix overwritten by the lookup.
    cur = db.execute(
        "UPDATE journal_entries SET latitude=?, longitude=?, location_source='trail'"
        ' WHERE id=? AND (latitude IS NULL OR longitude IS NULL)', (*found, entry_id))
    return cur.rowcount > 0


def fill_between(db, start: float, end: float) -> int:
    """Entries from [start, end] (± the lookup gap) still unlocated — for a
    trail upload that arrived after the entries did, which is the usual order:
    the phone uploads its trail every ten minutes or so."""
    rows = db.execute(
        'SELECT id FROM journal_entries WHERE (latitude IS NULL OR longitude IS NULL)'
        ' AND created_at BETWEEN ? AND ?', (start - MAX_GAP_S, end + MAX_GAP_S)).fetchall()
    filled = sum(1 for r in rows if fill(db, r['id']))
    if filled:
        db.commit()
    return filled
