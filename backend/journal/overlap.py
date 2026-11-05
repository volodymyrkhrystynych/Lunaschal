"""Join automatic screenshot runs to recordings with known capture intervals.

The recording keeps its client-minted entry ID, including across offline
replays. Only untouched screenshot entries are removed; user-authored entries
are never inferred to be duplicates from their time alone.
"""
import math


def duration_seconds(value):
    if value is None or value == '':
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise ValueError('durationSeconds must be a positive finite number')
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('durationSeconds must be a positive finite number')
    return seconds


def recording_for_interval(db, start, end):
    """Ambiguous simultaneous recordings stay separate for manual merging."""
    rows = db.execute(
        "SELECT DISTINCT a.entry_id FROM journal_attachments a"
        " JOIN journal_entries j ON j.id=a.entry_id"
        " WHERE a.kind='audio' AND a.duration_seconds > 0"
        " AND a.created_at <= ? AND a.created_at + a.duration_seconds >= ?"
        " AND j.idea_id IS NULL AND NOT EXISTS"
        " (SELECT 1 FROM journal_entry_fic_refs f WHERE f.journal_entry_id=j.id)",
        (end, start),
    ).fetchall()
    return rows[0]['entry_id'] if len(rows) == 1 else None


def merge_screenshot_runs(db, entry_id):
    """Within the caller's capture lock, move eligible runs in one transaction.

    Returns removed IDs for feed invalidation. The caller syncs the surviving
    screenshot event and commits. Media files retain their attachment IDs.
    """
    sessions = db.execute(
        "SELECT s.* FROM journal_screenshot_sessions s"
        " JOIN journal_entries j ON j.id=s.entry_id"
        " WHERE s.entry_id != ? AND COALESCE(j.content,'')=''"
        " AND EXISTS (SELECT 1 FROM journal_attachments r WHERE r.entry_id=?"
        " AND r.kind='audio' AND r.duration_seconds > 0"
        " AND r.created_at <= s.last_captured_at"
        " AND r.created_at + r.duration_seconds >= s.first_captured_at)"
        " AND COALESCE(j.raw_content,'')='' AND j.title='Screenshots'"
        " AND COALESCE(j.tags,'[]') IN ('','[]') AND j.idea_id IS NULL"
        " AND NOT EXISTS (SELECT 1 FROM journal_entry_fic_refs f"
        " WHERE f.journal_entry_id=j.id)"
        " AND NOT EXISTS (SELECT 1 FROM journal_attachments a"
        " WHERE a.entry_id=j.id AND a.kind != 'image')"
        " AND NOT EXISTS (SELECT 1 FROM journal_voice_drafts d WHERE d.entry_id=j.id)"
        " AND NOT EXISTS (SELECT 1 FROM journal_entry_curated_tags t"
        " WHERE t.entry_id=j.id)",
        (entry_id, entry_id),
    ).fetchall()
    removed = []
    for session in sessions:
        if recording_for_interval(db, session['first_captured_at'],
                                  session['last_captured_at']) != entry_id:
            continue
        source = session['entry_id']
        target = db.execute(
            'SELECT * FROM journal_screenshot_sessions WHERE entry_id=?',
            (entry_id,),
        ).fetchone()
        db.execute('UPDATE journal_attachments SET entry_id=? WHERE entry_id=?',
                   (entry_id, source))
        db.execute('UPDATE calendar_events SET journal_id=? WHERE journal_id=?',
                   (entry_id, source))
        # Keep one session/event for the combined screenshot span. Other
        # calendar events attached by the user remain linked to the survivor.
        if target is None:
            db.execute('UPDATE journal_screenshot_sessions SET entry_id=?, is_open=0'
                       ' WHERE entry_id=?', (entry_id, source))
        else:
            db.execute('DELETE FROM journal_screenshot_sessions WHERE entry_id=?',
                       (source,))
            if session['calendar_event_id'] and not target['calendar_event_id']:
                db.execute('UPDATE journal_screenshot_sessions SET calendar_event_id=?'
                           ' WHERE entry_id=?', (session['calendar_event_id'], entry_id))
            elif session['calendar_event_id']:
                db.execute('DELETE FROM calendar_events WHERE id=?',
                           (session['calendar_event_id'],))
        db.execute('DELETE FROM journal_entries WHERE id=?', (source,))
        removed.append(source)
    return removed
