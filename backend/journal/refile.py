"""Move a journal entry into the food log or the spending ledger.

The second half of auto-filing (`backend/ai/autofile.py` is the first): a phone
capture saved with the Capture tab's Save button turned out to be a meal or a
purchase, so it becomes one — the same id, the same media ids, the files moved
rather than copied, and the journal row gone.

**The ids are kept because the phone is still talking about them.** The phone
holds a capture until every part of it is acknowledged and replays anything it
is unsure of, so after a move it can still POST the entry's create, a clip or a
file against the journal id. `journal_refiles` is what lets those routes answer
the replay with the 201 it expects (see `forwarded_ack`) instead of a 404 that
fails the capture, or an `INSERT OR IGNORE` that quietly brings an empty journal
entry back. Keeping the ids is also what makes the phone's feed hide its local
copy: it hides a capture whose id is on the server in any of the three logs.

**Anything that has started pointing at the entry keeps it in the journal.** A
curated tag, a calendar link, a fic or idea link, a review card: each is a
decision someone made about this entry *as a journal entry*, and none of them
can follow it into a meal. A move is a guess and those are not, so the guess
loses. Likewise an attachment no meal or receipt can hold (a file, a YouTube
link), or one whose reading is still in flight.
"""
import logging
import shutil
import time
from pathlib import Path

from backend.ai import jobs
from backend.journal import storage as journal_storage

logger = logging.getLogger(__name__)

TARGETS = {'food': 'food_entries', 'spending': 'spending_logs'}

_MEDIA_TABLES = {'food_entries': 'food_media', 'spending_logs': 'spending_media'}

# What each log can hold, by journal attachment kind.
_ACCEPTS = {'food': {'image', 'video', 'audio'}, 'spending': {'image', 'audio'}}

# What, once it points at a journal entry, pins it there. One query per row.
_REFERENCES = (
    'SELECT 1 FROM journal_entry_curated_tags WHERE entry_id=?',
    'SELECT 1 FROM calendar_journal_links WHERE journal_entry_id=?',
    'SELECT 1 FROM journal_entry_fic_refs WHERE journal_entry_id=?',
    'SELECT 1 FROM journal_screenshot_sessions WHERE entry_id=?',
    'SELECT 1 FROM journal_voice_drafts WHERE entry_id=?',
    "SELECT 1 FROM learning_cards WHERE source_type='journal_review' AND source_id=?",
    'SELECT 1 FROM journal_entries WHERE id=? AND idea_id IS NOT NULL',
)


def refiled_to(db, entry_id: str) -> str | None:
    """The table a moved journal entry now lives in, or None."""
    row = db.execute(
        'SELECT target_collection FROM journal_refiles WHERE entry_id=?', (entry_id,)
    ).fetchone()
    return row['target_collection'] if row else None


def forwarded_media(db, entry_id: str, media_id: str) -> bool:
    """Whether `media_id` is already stored on the entry `entry_id` moved to."""
    target = refiled_to(db, entry_id)
    if target is None:
        return False
    return db.execute(
        f'SELECT 1 FROM {_MEDIA_TABLES[target]} WHERE id=? AND entry_id=?',
        (media_id, entry_id),
    ).fetchone() is not None


def media_owner(db, media_id: str) -> str | None:
    """The moved entry a media id was carried to, if it was carried by a move."""
    for table in _MEDIA_TABLES.values():
        row = db.execute(
            f'SELECT m.entry_id FROM {table} m JOIN journal_refiles r ON r.entry_id = m.entry_id'
            ' WHERE m.id=?', (media_id,),
        ).fetchone()
        if row:
            return row['entry_id']
    return None


def moveable_to(db, entry_id: str) -> set[str]:
    """Which of 'food'/'spending' this entry could become right now."""
    if db.execute('SELECT 1 FROM journal_entries WHERE id=?', (entry_id,)).fetchone() is None:
        return set()
    for sql in _REFERENCES:
        if db.execute(sql, (entry_id,)).fetchone():
            return set()
    rows = db.execute(
        'SELECT kind, transcript_status, description_status, import_status'
        ' FROM journal_attachments WHERE entry_id=?', (entry_id,),
    ).fetchall()
    if any('running' in (r['transcript_status'], r['description_status'])
           or r['import_status'] == 'importing' for r in rows):
        return set()
    kinds = {r['kind'] for r in rows}
    return {target for target, accepted in _ACCEPTS.items() if kinds <= accepted}


def refile_entry(db, entry_id: str, target: str) -> bool:
    """Move the journal entry to `target` ('food' or 'spending'). True if moved.

    Files move first and the rows after, in one transaction; a failure part way
    puts every file back, so the entry is either wholly a journal entry or
    wholly the new one.
    """
    from backend.food import storage as food_storage
    from backend.spending import storage as spending_storage

    if target not in TARGETS or target not in moveable_to(db, entry_id):
        return False
    entry = db.execute('SELECT * FROM journal_entries WHERE id=?', (entry_id,)).fetchone()
    attachments = db.execute(
        'SELECT * FROM journal_attachments WHERE entry_id=? ORDER BY position, created_at, id',
        (entry_id,),
    ).fetchall()
    collection = TARGETS[target]
    if db.execute(f'SELECT 1 FROM {collection} WHERE id=?', (entry_id,)).fetchone():
        return False
    media_path = (food_storage if target == 'food' else spending_storage).media_path

    # Plan every move before making one: an extension the target refuses is a
    # reason to stay in the journal, not to arrive half-moved.
    plan: list[tuple[object, Path, Path]] = []
    for a in attachments:
        src = journal_storage.resolve_stored_path(a['path'])
        if src is None or not src.is_file():
            return False
        dst = media_path(entry_id, a['id'], src.suffix.lstrip('.'))
        if dst is None:
            return False
        plan.append((a, src, dst))

    moved: list[tuple[Path, Path]] = []
    try:
        for _, src, dst in plan:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved.append((src, dst))
        _write_rows(db, entry, target, plan)
        db.commit()
    except Exception:
        db.rollback()
        for src, dst in reversed(moved):
            try:
                shutil.move(str(dst), str(src))
            except OSError as e:
                logger.error('Could not put %s back after a failed refile: %s', src, e)
        logger.exception('Refiling journal entry %s as %s failed', entry_id, target)
        return False

    for a, _, _ in plan:
        journal_storage.delete_attachment_dir(a['id'])
    _after_move(db, entry_id, target, [(a, dst) for a, _, dst in plan])
    logger.info('Auto-filed journal entry %s as %s (%d attachments)',
                entry_id, target, len(plan))
    return True


def _write_rows(db, entry, target: str, plan) -> None:
    from backend.day_boundary import day_key_for
    from backend.routes.journal import delete_journal_entry

    entry_id = entry['id']
    now = int(time.time())
    # Typed text is in `content` with no `raw_content`; a transcript appended
    # since is in both. Either way this is what was said, unpolished.
    text = (entry['raw_content'] or entry['content'] or '').strip() or None
    if target == 'food':
        db.execute(
            'INSERT INTO food_entries(id, raw_content, latitude, longitude, weather,'
            ' weather_checked_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)',
            (entry_id, text, entry['latitude'], entry['longitude'], entry['weather'],
             entry['weather_checked_at'], entry['created_at'], now),
        )
    else:
        db.execute(
            'INSERT INTO spending_logs(id, date, raw_content, latitude, longitude,'
            ' created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
            (entry_id, day_key_for(entry['created_at']), text, entry['latitude'],
             entry['longitude'], entry['created_at'], now),
        )
    table = _MEDIA_TABLES[TARGETS[target]]
    for position, (a, _, dst) in enumerate(plan):
        # A transcript is carried: it is the same speech model either way, and
        # it is already in `text`. A photo's caption is not — each log reads
        # its pictures with its own prompt (a receipt's total, a dish's name),
        # so the target's own reading is queued after the move instead.
        transcribed = a['kind'] == 'audio' and a['transcript_status'] == 'done'
        db.execute(
            f'INSERT INTO {table}(id, entry_id, kind, path, mime, position, transcript,'
            ' transcript_status, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
            (a['id'], entry_id, a['kind'], str(dst), a['mime'], position,
             a['transcript'] if transcribed else None,
             'done' if transcribed else 'idle', a['created_at']),
        )
    db.execute(
        'INSERT INTO journal_refiles(entry_id, target_collection, refiled_at) VALUES (?,?,?)',
        (entry_id, TARGETS[target], now),
    )
    # Work queued for the journal row would run against nothing.
    ids = [entry_id] + [a['id'] for a, _, _ in plan]
    db.execute(
        "DELETE FROM llm_jobs WHERE status='pending' AND kind LIKE 'journal.%'"
        f" AND target_id IN ({','.join('?' * len(ids))})", ids,
    )
    db.execute('DELETE FROM journal_attachments WHERE entry_id=?', (entry_id,))
    delete_journal_entry(db, entry_id)


def _after_move(db, entry_id: str, target: str, media: list) -> None:
    """What the target's own create route does once its rows are in."""
    from backend.ai.images import is_vision_configured
    from backend.weather import entry as entry_weather

    images = [dst for a, dst in media if a['kind'] == 'image']
    clips = [(a, dst) for a, dst in media
             if a['kind'] == 'audio' and a['transcript_status'] != 'done']
    if target == 'food':
        from backend.food import location as meal_location
        from backend.routes import food

        meta = food._photo_meta_from(images)
        updates: dict = {}
        if meta['taken_at']:
            updates['created_at'] = meta['taken_at']
        if meta['latitude'] is not None:
            updates['latitude'], updates['longitude'] = meta['latitude'], meta['longitude']
        if updates:
            from backend.db.connection import build_update
            build_update(db, 'food_entries', updates, 'id=?', (entry_id,))
        meal_location.fill(db, entry_id)
        if is_vision_configured():
            for a, _ in media:
                if a['kind'] == 'image':
                    food._queue_description(a['id'])
        for a, dst in clips:
            db.execute("UPDATE food_media SET transcript_status='running' WHERE id=?", (a['id'],))
            jobs.enqueue('food.transcribe_media', a['id'],
                         {'entry_id': entry_id, 'path': str(dst)}, commit=False)
        row = db.execute('SELECT raw_content FROM food_entries WHERE id=?', (entry_id,)).fetchone()
        if row and row['raw_content']:
            jobs.enqueue('food.structure', entry_id, {'text': row['raw_content']}, commit=False)
    else:
        from backend.routes import spending

        spending._apply_photo_meta(db, entry_id, images)
        if is_vision_configured():
            for a, _ in media:
                if a['kind'] == 'image':
                    spending._queue_description(a['id'])
        for a, _ in clips:
            db.execute("UPDATE spending_media SET transcript_status='running' WHERE id=?", (a['id'],))
            jobs.enqueue('spending.transcribe_media', a['id'], commit=False)
        row = db.execute('SELECT raw_content FROM spending_logs WHERE id=?', (entry_id,)).fetchone()
        if row and row['raw_content']:
            jobs.enqueue('spending.structure', entry_id, commit=False)
    db.commit()
    if target == 'food':
        entry_weather.nudge()
    from backend.routes.journal import _notify_subscribers
    _notify_subscribers(entry_id)


def store_forwarded(db, entry_id: str, file, media_id: str | None, *,
                    kind: str | None = None) -> bool:
    """Store an upload that arrived for a journal entry after it moved, on the
    entry it moved to. False when the target cannot hold it."""
    target = refiled_to(db, entry_id)
    if target is None:
        return False
    if target == 'food_entries':
        from backend.routes import food as module
        task = 'food.transcribe_media'
    else:
        from backend.routes import spending as module
        task = 'spending.transcribe_media'
    res = module._save_media_file(entry_id, file, module._next_media_position(db, entry_id),
                                  media_id, kind=kind)
    if res is None:
        # Either a replay that raced this one in, or a type the log refuses.
        return bool(media_id) and forwarded_media(db, entry_id, media_id)
    public, path, stored_kind = res
    if stored_kind == 'audio':
        table = _MEDIA_TABLES[target]
        db.execute(f"UPDATE {table} SET transcript_status='running' WHERE id=?", (public['id'],))
        payload = {'entry_id': entry_id, 'path': str(path)} if target == 'food_entries' else None
        jobs.enqueue(task, public['id'], payload, commit=False)
    db.execute(f'UPDATE {target} SET updated_at=? WHERE id=?', (int(time.time()), entry_id))
    db.commit()
    return True
