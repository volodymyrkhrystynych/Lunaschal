"""Explicit sync maintenance; never deletes domain rows or operation receipts."""
import time

from ulid import ULID

from .feed import database


def compact(*, keep_days=90, now=None):
    if type(keep_days) is not int or keep_days < 1:
        raise ValueError('keep_days must be a positive integer')
    cutoff_time = int(time.time() if now is None else now) - keep_days * 86400
    with database(write=True) as db:
        cutoff = db.execute('SELECT COALESCE(MAX(sequence),0) FROM mobile_sync_changes WHERE created_at<?', (cutoff_time,)).fetchone()[0]
        if not cutoff:
            return 0
        removed = db.execute('''
            DELETE FROM mobile_sync_changes WHERE sequence<=? AND sequence NOT IN (
                SELECT MAX(sequence) FROM mobile_sync_changes WHERE sequence<=?
                GROUP BY collection,record_id
            )
        ''', (cutoff, cutoff)).rowcount
        db.execute('UPDATE mobile_sync_state SET history_floor=MAX(history_floor,?)', (cutoff,))
        return removed


def rotate_epoch():
    """Run after restoring a server DB, before accepting any client sync.

    Comparing cursor sequence alone cannot detect a restored timeline which
    later grows beyond an old device's cursor. Rotation makes the fork explicit.
    """
    epoch = str(ULID())
    with database(write=True) as db:
        db.execute('UPDATE mobile_sync_state SET id=?', (epoch,))
    return epoch
