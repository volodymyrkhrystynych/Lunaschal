"""One-time catch-up for recordings that predate the titling rules.

Two populations, and they are not the same rows.

**Clips with no description.** `_store_attachment` has described every audio and
video upload on arrival for a while now, but only while `is_audio_configured()`
was true — before the omni preset was configured, and through the stretch where
the alias named a model the router could not load, clips landed `idle` or
`error` and nothing ever went back for them. Their description is now an input
to the entry's title, so those are rows whose title is missing a line it would
have had.

**Entries with no title at all.** `POST /api/journal/recordings` (the bottom
bar's Record button) never asked for one: it writes an entry with an empty body
and returns, and `_generate_metadata_bg` is not on that path. Going forward a
description or a transcript landing retitles through `_retitle_bg`, but nothing
retroactively fires for a clip that was described months ago.

Deliberately a *run-once* pass behind a button rather than a scheduler. It is
bounded work over a fixed backlog — when it is done there is nothing left for it
to do, and a loop that wakes nightly to find nothing is a loop that will one day
wake and find something it should not have touched.

The progress dict is the curated-tag scan's shape (`backend/routes/curated_tags.py`),
including its most important rule: **a model that cannot answer stops the pass
rather than finishing it.** A paused GPU is not "this clip has no description".
"""
import logging
import threading

from backend.db.connection import get_db

logger = logging.getLogger(__name__)

# One pass at a time, and its state. A dict rather than a bare flag because the
# UI shows counts while it runs and the outcome after it stops.
_progress: dict = {'running': False}
_lock = threading.Lock()


def status() -> dict:
    with _lock:
        return dict(_progress)


def _set(**fields) -> None:
    with _lock:
        _progress.update(fields)


def _undescribed_attachments() -> list[dict]:
    """Recordings whose description was never written, newest first.

    `error` rows are included on purpose: every one of them failed against a
    model alias that no longer exists, so leaving them out would be preserving
    the record of a misconfiguration rather than fixing it. A row whose file is
    gone is skipped by the worker, not here — one stat per row would double the
    cost of building this list.
    """
    rows = get_db().execute(
        'SELECT id, entry_id, path, name FROM journal_attachments'
        " WHERE kind IN ('audio','video')"
        "   AND description_status IN ('idle','error')"
        "   AND (description IS NULL OR description = '')"
        ' ORDER BY created_at DESC'
    ).fetchall()
    return [dict(r) for r in rows]


def _untitled_recording_entries() -> list[str]:
    """Entries carrying a recording that have never been titled.

    Not restricted to an empty body: an entry can be untitled for more reasons
    than the Record button (a create whose metadata pass failed, a polish that
    never ran), and every one of them is an entry this pass can now do something
    about. Restricted to *having a recording*, because that is what this pass
    has new information about — a bare text entry with no title is the titling
    path's business, not this one's.
    """
    rows = get_db().execute(
        'SELECT DISTINCT e.id FROM journal_entries e'
        ' JOIN journal_attachments a ON a.entry_id = e.id'
        "   AND a.kind IN ('audio','video')"
        " WHERE e.title IS NULL OR e.title = ''"
        ' ORDER BY e.created_at DESC'
    ).fetchall()
    return [r['id'] for r in rows]


def counts() -> dict:
    """What a run would have to do, for the button to say so before it is
    pressed. Two queries, no model, cheap enough to answer on every poll."""
    return {
        'undescribed': len(_undescribed_attachments()),
        'untitled': len(_untitled_recording_entries()),
    }


def start() -> bool:
    """Begin a pass. False if one is already running."""
    with _lock:
        if _progress.get('running'):
            return False
        _progress.clear()
        _progress.update({'running': True, 'phase': 'describing', 'processed': 0,
                          'total': 0, 'described': 0, 'titled': 0,
                          'failed': 0, 'stopped': None})
    threading.Thread(target=_run, daemon=True,
                     name='journal-recording-backfill').start()
    return True


def _run() -> None:
    from backend.ai import service
    from backend.routes import journal as journal_routes

    try:
        attachments = _undescribed_attachments()
        # Captured *before* the describe loop, because describing retitles: a
        # description landing fires `_retitle_bg` on its own, which is the whole
        # point of the going-forward change. Asked afterwards this list would
        # come back empty and the pass would report titling nothing while having
        # titled everything.
        entries = _untitled_recording_entries()
        _set(total=len(attachments))
        for a in attachments:
            if journal_routes._resolve_attachment_path(a['path']) is None:
                # The row outlived its file. Nothing to describe, and nothing
                # to record as a failure of this pass.
                _bump('processed')
                continue
            try:
                with service.background():
                    journal_routes._describe_attachment_bg(
                        a['id'], a['entry_id'], a['path'], a['name'], now=True,
                    )
            except (service.InferencePaused, service.Preempted) as e:
                # Stand down and say why, the way the curated-tag scan does.
                # Marching on would record "no description" for every remaining
                # clip, which is a definitive-looking answer to a question the
                # model was never asked.
                _finish(stopped=str(e) or 'inference unavailable')
                return
            except Exception as e:  # noqa: BLE001 — one bad clip is not the pass
                logger.warning('backfill: describing %s failed: %s', a['id'], e)
                _bump('failed')
                _bump('processed')
                continue
            # Read the row back rather than trusting the call to have raised.
            # `_describe_attachment_bg` catches its own failures and records
            # them as `description_status='error'` — so that a clip the model
            # cannot read does not break the entry it belongs to — which means
            # a failure arrives here as a perfectly ordinary return.
            _bump('described' if _has_description(a['id']) else 'failed')
            _bump('processed')

        # Titles second, so an entry whose clip was just described is titled
        # with that description in hand rather than one pass too early.
        _set(phase='titling', processed=0, total=len(entries))
        for entry_id in entries:
            if _has_title(entry_id):
                # The describe half's own retitle got here first. It is titled,
                # and asking the model again would spend a GPU call to write the
                # same answer from the same inputs.
                _bump('titled')
                _bump('processed')
                continue
            try:
                with service.background():
                    # `_generate_metadata_bg` swallows every exception so a
                    # failed title cannot break the entry it was titling — which
                    # means a paused GPU reaches here as a silent no-op. The
                    # mark is the signal that survives that, and reading it is
                    # what `backend/ai/jobs.py`'s worker does for the same
                    # reason. Cleared first so a stale mark from an earlier
                    # entry cannot end the pass.
                    service.clear_deferral()
                    journal_routes._retitle_bg(entry_id, now=True)
                    deferred = service.deferred_reason()
                if deferred:
                    _finish(stopped=deferred)
                    return
            except (service.InferencePaused, service.Preempted) as e:
                _finish(stopped=str(e) or 'inference unavailable')
                return
            except Exception as e:  # noqa: BLE001
                logger.warning('backfill: titling %s failed: %s', entry_id, e)
                _bump('failed')
                _bump('processed')
                continue
            _bump('titled')
            _bump('processed')
        _finish()
    except Exception as e:  # noqa: BLE001 — the thread must not die silently
        logger.exception('backfill: pass failed')
        _finish(stopped=str(e) or 'failed')


def _has_description(attachment_id: str) -> bool:
    row = get_db().execute(
        'SELECT description FROM journal_attachments WHERE id=?', (attachment_id,)
    ).fetchone()
    return bool(row and (row['description'] or '').strip())


def _has_title(entry_id: str) -> bool:
    row = get_db().execute(
        'SELECT title FROM journal_entries WHERE id=?', (entry_id,)
    ).fetchone()
    return bool(row and (row['title'] or '').strip())


def _bump(field: str) -> None:
    with _lock:
        _progress[field] = _progress.get(field, 0) + 1


def _finish(stopped: str | None = None) -> None:
    _set(running=False, phase='done', stopped=stopped)
