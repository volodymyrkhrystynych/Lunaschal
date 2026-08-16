import os
import random
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

_DB_PATH = os.environ.get('DATABASE_URL', './data/lunaschal.db')
_conn: sqlite3.Connection | None = None

TIMESTAMP_COLS = frozenset({
    'created_at', 'updated_at', 'next_review', 'completed_at',
    'posted_at', 'last_checked_at', 'edited_at', 'started_at', 'ended_at', 'due',
    'generated_at', 'last_researched_at', 'assessed_at', 'answered_at',
    'researched_at', 'last_practiced_at', 'last_recall_at',
    'received_at', 'classified_at', 'last_synced_at', 'token_expires_at',
    'finished_at',
    # Job applications. 'fetched_at' also exists on email_images, which never
    # goes through row_to_dict today — and ISO is the right shape there too if
    # it ever does, so this is a widening rather than a special case.
    'applied_at', 'closed_at', 'purge_after', 'purged_at', 'fetched_at',
    'scanned_at',
})

CAMEL_CACHE: dict[str, str] = {}


def _to_camel(s: str) -> str:
    if s not in CAMEL_CACHE:
        parts = s.split('_')
        CAMEL_CACHE[s] = parts[0] + ''.join(p.capitalize() for p in parts[1:])
    return CAMEL_CACHE[s]


def mapping_to_dict(m: dict) -> dict:
    """Same conversion as `row_to_dict`, for plain dicts that never came
    straight off a cursor (e.g. calendar occurrences built by the expander)."""
    d = {}
    for key, val in m.items():
        if key in TIMESTAMP_COLS and val is not None:
            val = datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
        d[_to_camel(key)] = val
    return d


def row_to_dict(row: sqlite3.Row) -> dict:
    return mapping_to_dict({key: row[key] for key in row.keys()})


def build_update(
    db: sqlite3.Connection, table: str, updates: dict, where: str, params: tuple = ()
) -> sqlite3.Cursor:
    """Run `UPDATE {table} SET col=?, ... WHERE {where}` for a dict of column ->
    new value, appending `params` after the update values (typically the row id
    referenced by `where`, e.g. `build_update(db, 'recipes', updates, 'id=?', (id,))`).
    Returns the cursor (e.g. for `.rowcount`); the caller still calls `db.commit()`."""
    set_clause = ', '.join(f'{k}=?' for k in updates)
    return db.execute(
        f'UPDATE {table} SET {set_clause} WHERE {where}', [*updates.values(), *params]
    )


def get_db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute('PRAGMA journal_mode=WAL')
        _conn.execute('PRAGMA foreign_keys=ON')
    return _conn


def init_db() -> None:
    db = get_db()
    schema = (Path(__file__).parent / 'schema.sql').read_text()
    db.executescript(schema)
    db.commit()
    # Drop password_hash if it exists from an older schema
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'password_hash' in cols:
        db.execute('ALTER TABLE settings DROP COLUMN password_hash')
        db.commit()
    # Drop ollama_bg_model — CPU-inference background model concept removed
    if 'ollama_bg_model' in cols:
        db.execute('ALTER TABLE settings DROP COLUMN ollama_bg_model')
        db.commit()
    _init_fts(db)
    _init_recipes_fts(db)
    _init_wiki_fts(db)
    _init_fanfic_fts(db)
    _init_emails_fts(db)
    _drop_vector_tables(db)
    _ensure_network_code(db)
    _ensure_writing_project_id(db)
    _ensure_conversation_day_key(db)
    _ensure_conversation_mode(db)
    _ensure_websearch_settings(db)
    _ensure_stt_shortcuts(db)
    _ensure_stt_model_settings(db)
    _ensure_journal_raw_content(db)
    _migrate_flashcards_to_learning(db)
    _ensure_prevent_sleep(db)
    _ensure_nudge_settings(db)
    _ensure_briefing_settings(db)
    _ensure_research_settings(db)
    _ensure_timeout_settings(db)
    _ensure_message_finished_at(db)
    # After both column sets exist: it reads one and writes the other.
    _migrate_websearch_search_to_research(db)
    _ensure_idea_assessment_columns(db)
    _ensure_conversation_idea_id(db)
    _reset_stale_idea_research(db)
    _ensure_email_settings(db)
    _ensure_email_body_html(db)
    _ensure_job_settings(db)
    _ensure_llm_generation_settings(db)
    # Must run after the two above: it drops the graded reasoning_effort columns
    # they used to own, reading their values first.
    _ensure_llama_server_settings(db)
    # After it: llama_vision_model is a llama-server-era column, so there is no
    # point adding it to a settings table that migration is still reshaping.
    _ensure_llama_vision_model(db)
    _ensure_llama_audio_model(db)
    _ensure_llama_chat_vision(db)
    # After all four alias columns exist: it clears the ones naming presets that
    # llama/presets.ini no longer defines.
    _migrate_gemma_aliases_to_qwen36(db)
    _ensure_attachment_description_columns(db)
    _ensure_journal_attachment_location(db)
    _ensure_todo_completed_at(db)
    _ensure_todo_list_columns(db)
    _ensure_todo_priority(db)
    _merge_chores_into_todos(db)
    _ensure_task_event_columns(db)
    _ensure_calendar_recurrence(db)
    _ensure_calendar_categories(db)
    _ensure_fic_review_columns(db)
    _ensure_fic_folder_position(db)
    _ensure_fic_update_pending(db)
    _ensure_fic_deep_scan_columns(db)
    _ensure_site_cookie_user_agent(db)
    _repair_escaped_image_fallbacks(db)
    _ensure_paper_archive_requested(db)
    _ensure_food_location(db)
    _ensure_food_recipe_match_status(db)
    _ensure_hf_token(db)
    _ensure_meeting_speaker_names(db)
    _ensure_meeting_echo_cancel(db)
    _ensure_meeting_source(db)
    _ensure_meeting_pause_columns(db)
    _ensure_meeting_whisper_columns(db)
    _migrate_workout_intensity_to_stars(db)
    _ensure_message_status(db)
    _ensure_message_raw_content(db)
    _ensure_chat_attachment_location(db)
    _ensure_practice_recall_columns(db)
    _ensure_learning_attempts_speech_requested(db)
    _reset_stale_fic_downloads(db)
    _reset_stale_meetings(db)
    _reset_stale_attachment_transcripts(db)
    _reset_stale_chat_attachment_descriptions(db)
    _reset_stale_message_runs(db)


def _ensure_network_code(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'network_code' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN network_code TEXT')
        db.commit()
    row = db.execute('SELECT id, network_code FROM settings LIMIT 1').fetchone()
    now = int(time.time())
    code = str(random.randint(100000, 999999))
    if row is None:
        db.execute(
            'INSERT INTO settings(id, network_code, created_at, updated_at) VALUES(1, ?, ?, ?)',
            (code, now, now),
        )
        db.commit()
    elif not row['network_code']:
        db.execute('UPDATE settings SET network_code=? WHERE id=1', (code,))
        db.commit()


def _ensure_stt_shortcuts(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'stt_paste_key' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN stt_paste_key TEXT')
        db.commit()
    if 'stt_voice_key' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN stt_voice_key TEXT')
        db.commit()
    if 'stt_journal_key' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN stt_journal_key TEXT')
        db.commit()


def _ensure_stt_model_settings(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    for col in ('stt_backend', 'tts_backend', 'whisper_model', 'stt_device'):
        if col not in cols:
            db.execute(f'ALTER TABLE settings ADD COLUMN {col} TEXT')
    if 'voice_pipeline_enabled' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN voice_pipeline_enabled INTEGER DEFAULT 1')
    db.commit()


def _ensure_todo_completed_at(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(todos)')}
    if 'completed_at' not in cols:
        db.execute('ALTER TABLE todos ADD COLUMN completed_at INTEGER')
        # Best guess for todos completed before this column existed: their
        # last update was the moment they were checked off.
        db.execute('UPDATE todos SET completed_at=updated_at WHERE done=1')
        db.commit()


def _ensure_task_event_columns(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(task_events)')}
    added = False
    # Which list the task lived on ('todo'/'chores'/'archive' or 'daily'), shown
    # as a qualifier on the Journal notification.
    if 'task_list' not in cols:
        db.execute('ALTER TABLE task_events ADD COLUMN task_list TEXT')
        added = True
    # Snapshot of the task's notes, revealed when the notification is expanded.
    if 'detail' not in cols:
        db.execute('ALTER TABLE task_events ADD COLUMN detail TEXT')
        added = True
    if added:
        db.commit()


def _ensure_todo_priority(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(todos)')}
    if 'priority' not in cols:
        # 1 (very unimportant) … 5 (very important); 3 is neutral so existing
        # todos keep their relative order until a priority is set.
        db.execute('ALTER TABLE todos ADD COLUMN priority INTEGER NOT NULL DEFAULT 3')
        db.commit()


def _ensure_todo_list_columns(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(todos)')}
    added = False
    for col, decl in (
        ('list', "TEXT NOT NULL DEFAULT 'todo'"),
        ('notes', 'TEXT'),
        ('due', 'INTEGER'),
        ('repeat_interval', 'INTEGER'),
        ('repeat_unit', 'TEXT'),
    ):
        if col not in cols:
            db.execute(f'ALTER TABLE todos ADD COLUMN {col} {decl}')
            added = True
    if added:
        db.commit()


def _merge_chores_into_todos(db: sqlite3.Connection) -> None:
    """Fold the retired 'chores' list into 'todo'.

    Chores were never their own table — just todos with list='chores' — and the
    Lifestyle tab now shows one merged list, so the discriminator has nothing
    left to discriminate. Idempotent by construction: nothing can write 'chores'
    any more (`normalize_list` folds it at the API boundary), so the second run
    matches no rows.

    `task_events.task_list` is deliberately left alone. Those completions really
    did come off a Chores list, and the Journal feed still labels them that way.
    """
    changed = db.execute("UPDATE todos SET list='todo' WHERE list='chores'").rowcount
    if changed:
        db.commit()


def _ensure_calendar_recurrence(db: sqlite3.Connection) -> None:
    """Recurrence rule and all-day columns on calendar_events.

    The exceptions table itself is a plain CREATE TABLE IF NOT EXISTS in
    schema.sql; only the added columns need the ALTER dance.
    """
    cols = {r[1] for r in db.execute('PRAGMA table_info(calendar_events)')}
    added = False
    for col, decl in (
        ('repeat_freq', 'TEXT'),
        ('repeat_interval', 'INTEGER'),
        ('repeat_byweekday', 'TEXT'),
        ('repeat_until', 'TEXT'),
        # Backfills to 0, so events that predate the flag stay merely untimed
        # rather than being retroactively relabelled as all-day.
        ('all_day', 'INTEGER NOT NULL DEFAULT 0'),
        # No REFERENCES here: SQLite can't add an FK constraint to an existing
        # table via ALTER, and the column is a breadcrumb, not an invariant.
        ('split_from', 'TEXT'),
    ):
        if col not in cols:
            db.execute(f'ALTER TABLE calendar_events ADD COLUMN {col} {decl}')
            added = True
    if added:
        db.commit()


def _ensure_calendar_categories(db: sqlite3.Connection) -> None:
    """AI-assigned category tags on calendar_events (leisure/work/exercise/
    family/outside/indoors — see backend/ai/calendar.py), kept separate from
    the existing free-text `tags` column so a user-typed pill can never
    collide with a classifier result.

    `classified_at IS NULL` is the "still pending" state, for both
    never-transcribed events and previously-failed classifications — same
    idiom as `emails.classified_at` (backend/ai/email.py), so a crash
    mid-classification needs no separate in-progress flag to reset.
    """
    cols = {r[1] for r in db.execute('PRAGMA table_info(calendar_events)')}
    added = False
    for col, decl in (
        ('category_tags', 'TEXT'),
        ('classified_at', 'INTEGER'),
        ('classification_error', 'TEXT'),
    ):
        if col not in cols:
            db.execute(f'ALTER TABLE calendar_events ADD COLUMN {col} {decl}')
            added = True
    if added:
        db.commit()


def _ensure_fic_review_columns(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(fics)')}
    if 'rating' not in cols:
        db.execute('ALTER TABLE fics ADD COLUMN rating INTEGER CHECK(rating BETWEEN 1 AND 5)')
    if 'review' not in cols:
        db.execute('ALTER TABLE fics ADD COLUMN review TEXT')
    db.commit()


def _ensure_fic_folder_position(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(fic_folders)')}
    if 'position' not in cols:
        db.execute('ALTER TABLE fic_folders ADD COLUMN position INTEGER NOT NULL DEFAULT 0')
        # Backfill with the creation order the folder list used until now.
        rows = db.execute('SELECT id FROM fic_folders ORDER BY created_at, rowid').fetchall()
        db.executemany('UPDATE fic_folders SET position=? WHERE id=?',
                       [(i, r['id']) for i, r in enumerate(rows)])
        db.commit()


def _ensure_learning_attempts_speech_requested(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(learning_attempts)')}
    if 'speech_requested' not in cols:
        db.execute('ALTER TABLE learning_attempts ADD COLUMN speech_requested INTEGER NOT NULL DEFAULT 0')
        db.commit()


def _ensure_fic_update_pending(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(fics)')}
    if 'update_pending' not in cols:
        db.execute('ALTER TABLE fics ADD COLUMN update_pending INTEGER NOT NULL DEFAULT 0')
        db.commit()


def _ensure_fic_deep_scan_columns(db: sqlite3.Connection) -> None:
    """Columns for the deep update scan: whether the queued check should be a
    deep one, and the per-chapter edit timestamp that scan compares against.

    `edited_at` is left NULL on existing rows on purpose — a post carrying no
    "Last edited" notice parses to None too, so unchanged chapters compare
    equal and the first deep scan doesn't rewrite the whole library."""
    cols = {r[1] for r in db.execute('PRAGMA table_info(fics)')}
    if 'deep_pending' not in cols:
        db.execute('ALTER TABLE fics ADD COLUMN deep_pending INTEGER NOT NULL DEFAULT 0')
    chapter_cols = {r[1] for r in db.execute('PRAGMA table_info(fic_chapters)')}
    if 'edited_at' not in chapter_cols:
        db.execute('ALTER TABLE fic_chapters ADD COLUMN edited_at INTEGER')
    db.commit()


def _ensure_site_cookie_user_agent(db: sqlite3.Connection) -> None:
    """cf_clearance is validated by Cloudflare against the User-Agent that
    solved the challenge — a hardcoded scraper UA that doesn't match the
    browser session the cookie came from gets the request re-challenged
    even with an otherwise-valid cookie. Store the UA alongside each site's
    cookie so requests can be made with the browser's own value instead."""
    cols = {r[1] for r in db.execute('PRAGMA table_info(site_cookies)')}
    if 'user_agent' not in cols:
        db.execute('ALTER TABLE site_cookies ADD COLUMN user_agent TEXT')
        db.commit()


def _repair_escaped_image_fallbacks(db: sqlite3.Connection) -> None:
    """One-time cleanup of XenForo <noscript> image fallbacks left as escaped,
    visible markup by the pre-`clean_content_tags` sanitizer. The marker column
    keeps this off the startup path once it has run — the scan is over every
    chapter's HTML."""
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'fic_escaped_img_repair' in cols:
        return
    db.execute('ALTER TABLE settings ADD COLUMN fic_escaped_img_repair INTEGER NOT NULL DEFAULT 0')
    db.commit()

    from backend.fanfic.sanitize import (
        count_words, html_to_text, strip_escaped_image_fallbacks,
    )

    rows = db.execute(
        "SELECT id, content_html FROM fic_chapters WHERE content_html LIKE '%&lt;img%'"
    ).fetchall()
    for row in rows:
        fixed = strip_escaped_image_fallbacks(row['content_html'])
        if fixed == row['content_html']:
            continue
        text = html_to_text(fixed)
        db.execute(
            'UPDATE fic_chapters SET content_html=?, content_text=?, word_count=? WHERE id=?',
            (fixed, text, count_words(text), row['id']),
        )
    db.execute('UPDATE settings SET fic_escaped_img_repair=1')
    db.commit()


def _ensure_paper_archive_requested(db: sqlite3.Connection) -> None:
    if not db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='papers'").fetchone():
        return
    cols = {r[1] for r in db.execute('PRAGMA table_info(papers)')}
    if 'archive_requested_at' not in cols:
        db.execute('ALTER TABLE papers ADD COLUMN archive_requested_at INTEGER')
        db.commit()


def _ensure_food_location(db: sqlite3.Connection) -> None:
    if not db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='food_entries'").fetchone():
        return
    cols = {r[1] for r in db.execute('PRAGMA table_info(food_entries)')}
    if 'latitude' not in cols:
        db.execute('ALTER TABLE food_entries ADD COLUMN latitude REAL')
    if 'longitude' not in cols:
        db.execute('ALTER TABLE food_entries ADD COLUMN longitude REAL')
    db.commit()


def _ensure_food_recipe_match_status(db: sqlite3.Connection) -> None:
    """NULL = not checked yet for a homemade/existing-recipe match; 'proposed'
    = a link was offered in chat; 'none' = checked, nothing to offer. Purely an
    idempotency guard — see backend/food/recipe_match.py — so the background
    check never re-fires for the same entry."""
    if not db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='food_entries'").fetchone():
        return
    cols = {r[1] for r in db.execute('PRAGMA table_info(food_entries)')}
    if 'recipe_match_status' not in cols:
        db.execute('ALTER TABLE food_entries ADD COLUMN recipe_match_status TEXT')
        db.commit()


def _migrate_workout_intensity_to_stars(db: sqlite3.Connection) -> None:
    """Fold `workout_sessions.intensity_rating` from the old 1-10 RPE onto the
    1-5 star scale, exactly once.

    The mapping is ceil(v/2) — expressed here as SQLite integer division
    `(v + 1) / 2` — so 1-2 -> 1 … 9-10 -> 5.

    **This must never run twice**, and it can't be made self-detecting: "convert
    the rows above 5" would leave a genuine old 4 (light, ~2 stars) sitting at 4
    stars, and re-running the fold would crush a real 4 down to 2. So a marker
    column on `settings` records that it has happened, the same one-shot pattern
    `_repair_escaped_image_fallbacks` uses; its *existence* is the marker, so a
    settings table with no row still latches.
    """
    if not db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='workout_sessions'"
    ).fetchone():
        return
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'workout_intensity_five_star' in cols:
        return
    db.execute(
        'ALTER TABLE settings ADD COLUMN workout_intensity_five_star INTEGER NOT NULL DEFAULT 0'
    )
    db.execute(
        'UPDATE workout_sessions SET intensity_rating = (intensity_rating + 1) / 2'
        ' WHERE intensity_rating IS NOT NULL AND intensity_rating > 0'
    )
    db.execute('UPDATE settings SET workout_intensity_five_star=1')
    db.commit()


def _ensure_message_status(db: sqlite3.Connection) -> None:
    """`status`/`error` on `messages`, for chat replies that now generate on a
    background thread (backend/delegate/runs.py) instead of inside the request
    that streams them — a dropped connection no longer has to lose the reply,
    because the thread checkpoints it here regardless of who's listening.
    Existing rows default to 'done': every one of them is already a finished
    reply from before this column existed."""
    cols = {r[1] for r in db.execute('PRAGMA table_info(messages)')}
    if 'status' not in cols:
        db.execute(
            "ALTER TABLE messages ADD COLUMN status TEXT NOT NULL DEFAULT 'done'"
            " CHECK(status IN ('streaming','done','error'))"
        )
    if 'error' not in cols:
        db.execute('ALTER TABLE messages ADD COLUMN error TEXT')
    db.commit()


def _ensure_message_raw_content(db: sqlite3.Connection) -> None:
    """`raw_content` on `messages`: what was actually dictated, before the
    transcript-correction pass rewrote it and before the user edited it in the
    composer. Same contract as `journal_entries.raw_content` — NULL when the
    message was typed, and never overwritten once set. Existing rows stay NULL,
    which is correct: none of them went through a correction pass."""
    cols = {r[1] for r in db.execute('PRAGMA table_info(messages)')}
    if 'raw_content' not in cols:
        db.execute('ALTER TABLE messages ADD COLUMN raw_content TEXT')
    db.commit()


def _ensure_chat_attachment_location(db: sqlite3.Connection) -> None:
    """Device coordinates on a chat photo, as a fallback for its EXIF GPS.

    The EXIF is the better answer — it says where the photo was *taken* — but it
    is routinely absent: iOS re-encodes and strips location whenever an image
    goes through the clipboard or a share sheet, which is exactly what the chat
    composer's paste and drop paths produce. Measured on a real upload: the same
    iPhone photo arrived as plain JPEG with Make, Model, DateTime and GPSInfo all
    gone, where the Food tab's file picker delivered the untouched MPO original.

    Existing rows stay NULL, which is honest: nothing recorded a position for
    them."""
    cols = {r[1] for r in db.execute('PRAGMA table_info(chat_attachments)')}
    if 'latitude' not in cols:
        db.execute('ALTER TABLE chat_attachments ADD COLUMN latitude REAL')
    if 'longitude' not in cols:
        db.execute('ALTER TABLE chat_attachments ADD COLUMN longitude REAL')
    db.commit()


def _reset_stale_chat_attachment_descriptions(db: sqlite3.Connection) -> None:
    """Same reasoning as _reset_stale_attachment_transcripts: reading a photo
    runs on a background worker whose state dies with the process. 'error'
    rather than 'idle' because a chat attachment has no re-read button — the
    photo simply didn't make it into that turn's context, and the row should
    say so instead of claiming to still be working."""
    db.execute(
        "UPDATE chat_attachments SET description_status='error',"
        " description_error='Interrupted by an app restart.'"
        " WHERE description_status='running'"
    )
    db.commit()


def _reset_stale_fic_downloads(db: sqlite3.Connection) -> None:
    """A fic's in-memory download progress (backend/fanfic/download.py's
    `_dl_progress`) never survives a process restart, but the persisted
    `download_status='downloading'` row does — if the process died (or the
    dev server's autoreloader restarted it) mid-download, the fic is left
    permanently stuck 'downloading' with no thread left to finish it. Since
    this runs once at startup, before any download thread exists in this
    process, any row still marked 'downloading' here is necessarily orphaned."""
    db.execute(
        "UPDATE fics SET download_status='error',"
        " download_error='Interrupted by an app restart — click Update to retry.'"
        " WHERE download_status='downloading'"
    )
    db.commit()


def _reset_stale_attachment_transcripts(db: sqlite3.Connection) -> None:
    """Same reasoning as _reset_stale_fic_downloads: transcription/captioning
    runs on a background worker whose state dies with the process, so a row
    still 'running' at startup has no thread left behind it. Reset to 'idle'
    rather than 'error' — nothing was lost, the button is simply clickable
    again."""
    db.execute(
        "UPDATE journal_attachments SET transcript_status='idle'"
        " WHERE transcript_status='running'"
    )
    db.execute(
        "UPDATE journal_attachments SET description_status='idle'"
        " WHERE description_status='running'"
    )
    db.commit()


def _reset_stale_message_runs(db: sqlite3.Connection) -> None:
    """Same reasoning as _reset_stale_fic_downloads: a chat reply's generation
    thread (backend/delegate/runs.py) dies with the process, but a row it left
    'streaming' persists — reset to 'error' so the reply reads as failed
    rather than stuck forever, with whatever partial content it had checkpointed
    kept in place."""
    db.execute(
        "UPDATE messages SET status='error', error='Interrupted by an app restart.'"
        " WHERE status='streaming'"
    )
    db.commit()


def _ensure_hf_token(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'hf_token' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN hf_token TEXT')
        db.commit()


def _ensure_meeting_echo_cancel(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'meeting_echo_cancel' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN meeting_echo_cancel INTEGER DEFAULT 0')
        db.commit()


def _ensure_meeting_speaker_names(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(meetings)')}
    if 'speaker_names' not in cols:
        db.execute('ALTER TABLE meetings ADD COLUMN speaker_names TEXT')
        db.commit()


def _ensure_meeting_source(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(meetings)')}
    if 'source' not in cols:
        db.execute("ALTER TABLE meetings ADD COLUMN source TEXT NOT NULL DEFAULT 'live'")
        db.commit()


def _ensure_meeting_pause_columns(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(meetings)')}
    if 'pause_requested' not in cols:
        db.execute('ALTER TABLE meetings ADD COLUMN pause_requested INTEGER NOT NULL DEFAULT 0')
    if 'mic_offset_seconds' not in cols:
        db.execute('ALTER TABLE meetings ADD COLUMN mic_offset_seconds REAL NOT NULL DEFAULT 0')
    if 'mic_segments_partial' not in cols:
        db.execute('ALTER TABLE meetings ADD COLUMN mic_segments_partial TEXT')
    if 'system_offset_seconds' not in cols:
        db.execute('ALTER TABLE meetings ADD COLUMN system_offset_seconds REAL NOT NULL DEFAULT 0')
    if 'system_segments_partial' not in cols:
        db.execute('ALTER TABLE meetings ADD COLUMN system_segments_partial TEXT')
    db.commit()


def _ensure_meeting_whisper_columns(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(meetings)')}
    if 'whisper_model' not in cols:
        db.execute("ALTER TABLE meetings ADD COLUMN whisper_model TEXT NOT NULL DEFAULT 'large-v3'")
    if 'whisper_device' not in cols:
        db.execute("ALTER TABLE meetings ADD COLUMN whisper_device TEXT NOT NULL DEFAULT 'cpu'")
    db.commit()


def _reset_stale_meetings(db: sqlite3.Connection) -> None:
    """Meeting recordings (ffmpeg Popen handles) and transcription threads never
    survive a process restart, but the persisted status does — any row still
    'recording' or 'transcribing' at startup is necessarily orphaned, UNLESS it
    was deliberately paused (checkpointed cleanly, no thread to lose) or is
    still awaiting the user to pick a model and start transcription (no thread
    was ever spawned for it in the first place).

    A 'recording' row has no checkpoint at all — the ffmpeg capture is simply
    gone — so its phase is reset to 'error' too. A 'transcribing' row DOES have
    a checkpoint (offset + partial segments, or a fully-finished track once
    past transcribing_mic), and _run()'s resume logic branches on the exact
    phase string to know which track to continue — so phase must be left
    untouched, exactly like _set_error does for an in-process exception.
    Only status/error are updated here; retry then resumes from the same
    point a mid-pipeline crash would have."""
    db.execute(
        "UPDATE meetings SET status='error', phase='error',"
        " error='Interrupted by an app restart.'"
        " WHERE status='recording'"
    )
    db.execute(
        "UPDATE meetings SET status='error',"
        " error='Interrupted by an app restart.'"
        " WHERE status='transcribing'"
        " AND phase NOT IN ('paused_mic','paused_system','awaiting_start')"
    )
    db.commit()


def _ensure_prevent_sleep(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'prevent_sleep' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN prevent_sleep INTEGER DEFAULT 0')
        db.commit()


def _ensure_nudge_settings(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'nudge_enabled' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN nudge_enabled INTEGER DEFAULT 1')
    if 'nudge_interval_minutes' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN nudge_interval_minutes INTEGER DEFAULT 45')
    db.commit()


def _ensure_briefing_settings(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'briefing_enabled' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN briefing_enabled INTEGER DEFAULT 1')
    if 'briefing_hour' not in cols:
        # Local hour the overnight briefing fires in; must be >= the 4am chat-day
        # rollover so the message lands in today's fresh conversation.
        db.execute('ALTER TABLE settings ADD COLUMN briefing_hour INTEGER DEFAULT 5')
    if 'briefing_model' not in cols:
        # Optional model override for the overnight briefing. NULL falls back to
        # the default chat model; the briefing runs while the user sleeps, so a
        # slower/larger model is fine here.
        db.execute('ALTER TABLE settings ADD COLUMN briefing_model TEXT')
    if 'briefing_goals' not in cols:
        # Free-text personal goals / current focus the user maintains; fed into
        # the briefing as standing context so the secretary knows what they're
        # working towards.
        db.execute('ALTER TABLE settings ADD COLUMN briefing_goals TEXT')
    # briefing_reasoning_effort is gone — thinking is now the boolean
    # briefing_thinking, owned by _ensure_llama_server_settings.
    if 'briefing_max_tokens' not in cols:
        # Output-token ceiling for the briefing completion. The briefing runs
        # overnight, so this is deliberately generous.
        db.execute('ALTER TABLE settings ADD COLUMN briefing_max_tokens INTEGER DEFAULT 16384')
    if 'briefing_num_ctx' not in cols:
        # Retired, twice over: the context window was first collapsed into one
        # shared setting, and is now fixed at llama-server load time and not
        # settable per request at all. Nothing reads this column; it stays only so
        # the migration remains an append-only ALTER.
        db.execute('ALTER TABLE settings ADD COLUMN briefing_num_ctx INTEGER DEFAULT 8192')
    db.commit()


def _ensure_research_settings(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'repo_context_enabled' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN repo_context_enabled INTEGER DEFAULT 1')
    if 'repo_context_hour' not in cols:
        # 03:00 deliberately: the chat-title sweep owns 02:00-03:00 and the
        # briefing owns 05:00-07:00, so a 03:00-05:00 window contends with
        # neither for the two llama slots.
        db.execute('ALTER TABLE settings ADD COLUMN repo_context_hour INTEGER DEFAULT 3')
    if 'research_search_provider' not in cols:
        # '' | 'brave' | 'searxng'. Empty means the research agent
        # has no web access and says so, rather than guessing.
        db.execute("ALTER TABLE settings ADD COLUMN research_search_provider TEXT DEFAULT ''")
    if 'research_search_key' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN research_search_key TEXT')
    if 'research_searxng_url' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN research_searxng_url TEXT')
    if 'research_enabled' not in cols:
        # Off by default: the research loop makes outbound web requests, which
        # is not something to start doing without being asked.
        db.execute('ALTER TABLE settings ADD COLUMN research_enabled INTEGER DEFAULT 0')
    db.commit()


def _ensure_timeout_settings(db: sqlite3.Connection) -> None:
    """Wall-clock budgets for a reply and for the research tools.

    Everything bounding these paths before now was a *count* -- turns, fetches,
    tokens -- so a run that took an hour was inside every limit it had. These
    are the deadlines, and each has its own number because the three are not
    the same kind of wait: a delegate web_search sits between Enter and the
    first token, a deep pass is expected to take a while, and the chat budget
    is the outer bound both of them are clamped to.
    """
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'chat_timeout_enabled' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN chat_timeout_enabled INTEGER DEFAULT 1')
    if 'chat_timeout_seconds' not in cols:
        # 15 minutes. Long enough that a slow but working reply is never cut
        # off, short enough that a wedged one does not hang the tab all day.
        db.execute('ALTER TABLE settings ADD COLUMN chat_timeout_seconds INTEGER DEFAULT 900')
    if 'research_timeout_enabled' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN research_timeout_enabled INTEGER DEFAULT 1')
    if 'research_search_timeout_seconds' not in cols:
        db.execute(
            'ALTER TABLE settings ADD COLUMN research_search_timeout_seconds INTEGER DEFAULT 120')
    if 'research_deep_timeout_seconds' not in cols:
        db.execute(
            'ALTER TABLE settings ADD COLUMN research_deep_timeout_seconds INTEGER DEFAULT 600')
    db.commit()


def _ensure_message_finished_at(db: sqlite3.Connection) -> None:
    """When a reply stopped generating, as opposed to when it was asked for.

    Deliberately a new column rather than moving `created_at`: the ULID/
    timestamp pair in backend/routes/chat.py's `_previous_user_message` and the
    transcript queries' ORDER BY both depend on created_at meaning "when the
    row was made".
    """
    cols = {r[1] for r in db.execute('PRAGMA table_info(messages)')}
    if 'finished_at' not in cols:
        db.execute('ALTER TABLE messages ADD COLUMN finished_at INTEGER')
    db.commit()


def _ensure_idea_assessment_columns(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(ideas)')}
    if 'assessment_id' not in cols:
        # Denormalized pointer at the newest idea_assessments row, so the list
        # endpoint does not need a correlated subquery per idea.
        db.execute('ALTER TABLE ideas ADD COLUMN assessment_id TEXT')
    if 'user_verdict' not in cols:
        # The user's own call, which always beats the agent's. Recording the
        # correction is what keeps the feature trustworthy.
        db.execute('ALTER TABLE ideas ADD COLUMN user_verdict TEXT')
    if 'user_verdict_note' not in cols:
        db.execute('ALTER TABLE ideas ADD COLUMN user_verdict_note TEXT')
    if 'research_state' not in cols:
        db.execute("ALTER TABLE ideas ADD COLUMN research_state TEXT DEFAULT 'idle'")
    if 'research_error' not in cols:
        db.execute('ALTER TABLE ideas ADD COLUMN research_error TEXT')
    if 'researched_at' not in cols:
        # When the background loop last researched this idea. Drives the
        # cooldown, so a quiet backlog doesn't get re-researched every tick.
        db.execute('ALTER TABLE ideas ADD COLUMN researched_at INTEGER')
    db.commit()


def _ensure_conversation_idea_id(db: sqlite3.Connection) -> None:
    """Second discriminator on conversations, after writing_project_id.

    Every query that means "a general chat conversation" now has to exclude
    both, or idea discussions surface in the Chat tab — see the six call sites
    that filter `writing_project_id IS NULL AND idea_id IS NULL`.
    """
    cols = {r[1] for r in db.execute('PRAGMA table_info(conversations)')}
    if 'idea_id' not in cols:
        db.execute(
            'ALTER TABLE conversations ADD COLUMN idea_id TEXT REFERENCES ideas(id)'
        )
        db.commit()


def _reset_stale_idea_research(db: sqlite3.Connection) -> None:
    """Same reasoning as _reset_stale_fic_downloads: a row still 'running' at
    startup has no worker thread left behind it. Reset to 'idle' rather than
    'error' — nothing was lost, the button is simply clickable again."""
    db.execute(
        "UPDATE ideas SET research_state='idle'"
        " WHERE research_state IN ('queued','running')"
    )
    db.commit()


def _ensure_email_settings(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'email_sync_enabled' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN email_sync_enabled INTEGER DEFAULT 1')
    if 'email_sync_interval_minutes' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN email_sync_interval_minutes INTEGER DEFAULT 15')
    if 'google_oauth_client_id' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN google_oauth_client_id TEXT')
    if 'google_oauth_client_secret' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN google_oauth_client_secret TEXT')
    db.commit()


def _ensure_job_settings(db: sqlite3.Connection) -> None:
    """Retention policy for tailored resumes, plus the singleton profile row.

    Defaults encode the two triggers the feature was asked for: purge six
    months after applying, and purge sooner once a rejection lands (after a
    grace period, so a resume is never yanked out from under a reply that is
    still being read).
    """
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'job_retention_days' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN job_retention_days INTEGER DEFAULT 180')
    if 'job_purge_on_rejection' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN job_purge_on_rejection INTEGER DEFAULT 1')
    if 'job_rejection_grace_days' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN job_rejection_grace_days INTEGER DEFAULT 30')
    db.commit()

    # The profile is read on every tailoring call and edited field by field, so
    # it is far simpler for it to always exist than for every caller to handle
    # its absence. INSERT OR IGNORE keeps that idempotent.
    now = int(time.time())
    db.execute(
        'INSERT OR IGNORE INTO job_profile (id, created_at, updated_at) VALUES (1, ?, ?)',
        (now, now),
    )
    db.commit()


def _ensure_email_body_html(db: sqlite3.Connection) -> None:
    """Add emails.body_html for mail synced before HTML was kept.

    Left empty rather than backfilled: the HTML is not recoverable from the
    stored text, so filling it would need a re-fetch of every message from
    Gmail. EmailDetail falls back to body_text when this is empty, so old
    mail keeps rendering exactly as it did — it just doesn't gain formatting
    until (and unless) it is re-synced.
    """
    cols = {r[1] for r in db.execute('PRAGMA table_info(emails)')}
    if 'body_html' not in cols:
        db.execute("ALTER TABLE emails ADD COLUMN body_html TEXT NOT NULL DEFAULT ''")
    db.commit()


def _ensure_llm_generation_settings(db: sqlite3.Connection) -> None:
    """Output ceiling for the default (conversational) model — the one the user
    chats with. Thinking lives in the boolean llm_thinking, owned by
    _ensure_llama_server_settings."""
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'llm_max_tokens' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN llm_max_tokens INTEGER DEFAULT 4096')
    if 'llm_num_ctx' not in cols:
        # Retired: the context window is fixed when llama-server loads the model
        # (ctx-size in llama/presets.ini) and cannot be set per request, so
        # nothing reads this column. It stays only so the migration remains an
        # append-only ALTER — see docs/learnings/local-model-context-budget.md.
        db.execute('ALTER TABLE settings ADD COLUMN llm_num_ctx INTEGER DEFAULT 8192')
    db.commit()


def _ensure_llama_chat_vision(db: sqlite3.Connection) -> None:
    """Whether the *chat* model can be sent an image directly.

    Separate from `llama_vision_model`, which names a different model for
    one-shot captioning. This is a property of the chat preset: Qwen3.6 35B A3B
    is a vision-language model (its GGUF carries mRoPE's four rope sections and
    the `image-text-to-text` tag), but `llama/presets.ini` ships with no `mmproj`
    on `[qwen36]` because the projector has to be downloaded and there is only
    ~878 MiB of VRAM headroom to load it into.

    Defaults to **0** for that reason: turning it on without a projector
    configured would send images to a model that cannot decode them. Ticking it
    is the deliberate act that follows a preset that actually loaded.
    """
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'llama_chat_vision' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN llama_chat_vision INTEGER DEFAULT 0')
    db.commit()


def _ensure_llama_vision_model(db: sqlite3.Connection) -> None:
    """Router alias for image captioning (journal photo attachments). Left NULL,
    which means captioning is off: the chat preset sets `mmproj-auto = false`
    and takes text only, so images go to a separate model that is a separate
    download. Defaulting this to anything would just produce a button that
    always errors. See backend/ai/images.py."""
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'llama_vision_model' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN llama_vision_model TEXT')
        db.commit()


def _ensure_llama_audio_model(db: sqlite3.Connection) -> None:
    """Router alias for non-speech audio description (journal audio/video
    attachments). Left NULL, same reasoning as llama_vision_model, and in
    practice it holds the same value: one any-to-any model covers images and
    audio both, so Settings writes the two columns together. They stay separate
    columns because they gate two independent features. Off by default until
    that preset is downloaded. See backend/ai/audio_description.py."""
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'llama_audio_model' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN llama_audio_model TEXT')
        db.commit()


def _migrate_gemma_aliases_to_qwen36(db: sqlite3.Connection) -> None:
    """Clear the retired `gemma4*` router aliases, exactly once.

    llama/presets.ini no longer defines `gemma4`, `gemma4-medium`, `gemma4-max`
    or `gemma4-e4b-audio` — the chat model is Qwen3.6 35B A3B and everything
    non-text is one CPU-resident `gemma4-12b-omni` preset. Nothing anywhere
    validates a stored alias against the router, so a settings row still naming
    a deleted section would 404 every call: the same failure `_ensure_llama_
    server_settings` refused to carry an Ollama tag into, for the same reason.

    So all four alias columns are nulled, and NULL is the right resting state
    for each:

    - `llama_model` — NULL means "whatever `DEFAULT_MODEL` says", which is now
      `qwen36`. Writing the new alias in instead would pin it, and a user who
      never chose a model should not end up with an explicit choice.
    - `briefing_model` — NULL means "same as the chat model".
    - `llama_vision_model` / `llama_audio_model` — NULL means the feature is
      off, which is correct: gemma-4-12b-it is a separate ~7.4 GB download that
      almost certainly is not on disk when this runs, and both
      `_ensure_llama_*_model` already argue that pointing these at an absent
      model "would just produce a button that always errors". One tick in
      Settings → llama.cpp turns them both back on.

    `llama_vision_model` also gets a *fix* out of this rather than only a
    reset: the value it held was `gemma4-vision`, a preset that never existed
    in presets.ini at all, so photo captioning has been failing at the router
    since the column was added.

    Latched on a marker column the way `_migrate_workout_intensity_to_stars`
    is — its existence is the marker, so this cannot undo a deliberate later
    choice, and a settings table with no row still latches.
    """
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'qwen36_aliases_migrated' in cols:
        return
    db.execute(
        'ALTER TABLE settings ADD COLUMN qwen36_aliases_migrated INTEGER NOT NULL DEFAULT 0'
    )
    # Listed exactly, not matched as a `gemma4%` prefix: `gemma4-12b-omni` is
    # also a gemma4 alias and is the one that survives the swap, so a prefix
    # would turn the multimodal toggle off the first time it was ticked.
    for col in ('llama_model', 'briefing_model', 'llama_vision_model', 'llama_audio_model'):
        if col in cols:
            db.execute(
                f'UPDATE settings SET {col} = NULL WHERE {col} IN'
                " ('gemma4', 'gemma4-medium', 'gemma4-max', 'gemma4-e4b-audio', 'gemma4-vision')"
            )
    db.execute('UPDATE settings SET qwen36_aliases_migrated=1')
    db.commit()


def _ensure_attachment_description_columns(db: sqlite3.Connection) -> None:
    """Non-speech audio/video description, kept separate from `transcript` —
    see the column comment in schema.sql."""
    cols = {r[1] for r in db.execute('PRAGMA table_info(journal_attachments)')}
    if 'description' not in cols:
        db.execute('ALTER TABLE journal_attachments ADD COLUMN description TEXT')
    if 'description_status' not in cols:
        db.execute(
            "ALTER TABLE journal_attachments ADD COLUMN description_status"
            " TEXT NOT NULL DEFAULT 'idle'"
        )
    if 'description_error' not in cols:
        db.execute('ALTER TABLE journal_attachments ADD COLUMN description_error TEXT')
    db.commit()


def _ensure_journal_attachment_location(db: sqlite3.Connection) -> None:
    """EXIF-derived capture location for image attachments — see the column
    comment in schema.sql."""
    cols = {r[1] for r in db.execute('PRAGMA table_info(journal_attachments)')}
    if 'latitude' not in cols:
        db.execute('ALTER TABLE journal_attachments ADD COLUMN latitude REAL')
    if 'longitude' not in cols:
        db.execute('ALTER TABLE journal_attachments ADD COLUMN longitude REAL')
    db.commit()


def _ensure_llama_server_settings(db: sqlite3.Connection) -> None:
    """Move the local-inference settings from Ollama to llama.cpp's llama-server.

    Two shape changes, not just a rename:

    - `ollama_url`/`ollama_model` become `llama_url`/`llama_model`. The port moves
      from 11434 to 8080 and the model name becomes a llama-server *router alias*
      (a section name in llama/presets.ini) rather than an Ollama tag, so the old
      value is deliberately NOT copied forward — 'qwen3.6:35b' is not a valid
      alias and silently keeping it would 404 every call. A NULL model means
      "whatever the router loads by default".
    - `llm_reasoning_effort`/`briefing_reasoning_effort` (Ollama's graded
      none/low/medium/high/max) collapse to booleans, because Gemma 4 has a single
      thinking channel toggled by a chat-template kwarg, with no levels.

      These two migrate *differently*, deliberately. The graded levels are not
      equivalent to Gemma 4's channel: a level bounded how much the old model
      thought, whereas Gemma 4's channel is unbounded, and measured here it costs
      **25s to first token instead of 1.3s** on a trivial prompt. So the
      interactive chat setting resets to off no matter what it was — a user who
      had 'high' did not ask for a 20x latency regression, and Settings makes it
      one click to opt back in. The briefing keeps its old intent, since it runs
      overnight where the tokens are free.
    """
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}

    if 'llama_url' not in cols:
        db.execute(
            "ALTER TABLE settings ADD COLUMN llama_url TEXT DEFAULT 'http://localhost:8080'"
        )
    if 'llama_model' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN llama_model TEXT')
    for dead in ('ollama_url', 'ollama_model'):
        if dead in cols:
            db.execute(f'ALTER TABLE settings DROP COLUMN {dead}')

    # briefing_model holds the same kind of value as the retired ollama_model — an
    # Ollama tag like 'qwen3.6:35b' — and survives in its own column, so it has to
    # be cleared on the same grounds: it is not a router alias, and leaving it
    # would 404 every overnight briefing while the interactive chat kept working,
    # which is a miserable thing to debug. NULL means "same as the chat model".
    # Keyed off the presence of the old columns so a user who later picks a real
    # alias keeps it.
    if 'ollama_model' in cols and 'briefing_model' in cols:
        db.execute("UPDATE settings SET briefing_model = NULL"
                   " WHERE briefing_model IS NOT NULL")

    # Graded effort -> boolean. `carry_over` says whether the old intent survives:
    # the briefing's does, the latency-sensitive chat path's does not (see above).
    for old, new, carry_over in (
        ('llm_reasoning_effort', 'llm_thinking', False),
        ('briefing_reasoning_effort', 'briefing_thinking', True),
    ):
        if new in cols:
            continue
        db.execute(f'ALTER TABLE settings ADD COLUMN {new} INTEGER DEFAULT 0')
        if old in cols:
            if carry_over:
                db.execute(
                    f"UPDATE settings SET {new} = CASE"
                    f" WHEN {old} IS NULL OR {old} = 'none' THEN 0 ELSE 1 END"
                )
            db.execute(f'ALTER TABLE settings DROP COLUMN {old}')
    db.commit()


def _ensure_journal_raw_content(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(journal_entries)')}
    if 'raw_content' not in cols:
        db.execute('ALTER TABLE journal_entries ADD COLUMN raw_content TEXT')
        db.commit()


def _migrate_flashcards_to_learning(db: sqlite3.Connection) -> None:
    # One-time move of legacy SM-2 flashcards into learning_cards; scheduling
    # resets (all due now, fresh FSRS). Dropping the table makes reruns no-ops.
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='flashcards'"
    ).fetchone()
    if not exists:
        return
    cols = {r[1] for r in db.execute('PRAGMA table_info(flashcards)')}
    tags_expr = 'tags' if 'tags' in cols else 'NULL'
    now = int(time.time())
    db.execute(
        f"""
        INSERT INTO learning_cards
            (id, question, answer, tags, source_type, source_id,
             state, fsrs_state, due, created_at, updated_at)
        SELECT id, front, back, {tags_expr},
               CASE WHEN source_id IS NOT NULL THEN 'journal' ELSE 'manual' END,
               source_id, 'active', NULL, ?, created_at, ?
        FROM flashcards
        """,
        (now, now),
    )
    db.execute('DROP TABLE flashcards')
    db.execute('DROP INDEX IF EXISTS idx_flashcard_next_review')
    db.commit()


def _ensure_practice_recall_columns(db: sqlite3.Connection) -> None:
    # Blind-drill counters on an existing practice_progress row. The two counts
    # default to 0 and the two "last recall" columns stay NULL, which is exactly
    # what modes.next_mode reads as "never asked for this one from memory" — so
    # a pre-existing row keeps its speed history and unlocks blind on the same
    # terms as a fresh one.
    cols = {r[1] for r in db.execute('PRAGMA table_info(practice_progress)')}
    if 'recall_attempts_count' not in cols:
        db.execute(
            'ALTER TABLE practice_progress ADD COLUMN recall_attempts_count INTEGER NOT NULL DEFAULT 0'
        )
    if 'recall_passes' not in cols:
        db.execute(
            'ALTER TABLE practice_progress ADD COLUMN recall_passes INTEGER NOT NULL DEFAULT 0'
        )
    if 'last_recall_passed' not in cols:
        db.execute('ALTER TABLE practice_progress ADD COLUMN last_recall_passed INTEGER')
    if 'last_recall_at' not in cols:
        db.execute('ALTER TABLE practice_progress ADD COLUMN last_recall_at INTEGER')
    db.commit()


def _ensure_writing_project_id(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(conversations)')}
    if 'writing_project_id' not in cols:
        db.execute('ALTER TABLE conversations ADD COLUMN writing_project_id TEXT REFERENCES writing_projects(id)')
        db.commit()


def _ensure_conversation_day_key(db: sqlite3.Connection) -> None:
    # day_key groups a conversation to a 4am-anchored local day (YYYY-MM-DD).
    # Left NULL on pre-existing conversations so they stay out of the journal feed.
    cols = {r[1] for r in db.execute('PRAGMA table_info(conversations)')}
    if 'day_key' not in cols:
        db.execute('ALTER TABLE conversations ADD COLUMN day_key TEXT')
        db.commit()


def _ensure_conversation_mode(db: sqlite3.Connection) -> None:
    # 'chat' | 'websearch' — distinguishes the two Chat-tab sub-tabs sharing
    # the same day_key/writing_project_id-scoped conversations. Defaulting to
    # 'chat' backfills every pre-existing row correctly: they were all the
    # regular chat before this column existed.
    cols = {r[1] for r in db.execute('PRAGMA table_info(conversations)')}
    if 'mode' not in cols:
        db.execute("ALTER TABLE conversations ADD COLUMN mode TEXT NOT NULL DEFAULT 'chat'")
        db.commit()


def _ensure_websearch_settings(db: sqlite3.Connection) -> None:
    """The retired web-search tab's own search-provider columns.

    The tab is gone — searching is now something the chat delegate decides to
    do, through the same backend/research/web.py the Ideas agent uses, so there
    is one search provider for the whole app and one place in Settings to
    configure it. These columns are kept (dropping one in SQLite means
    rebuilding the table, and an unread column costs nothing) and their values
    are folded into the research ones once, below.
    """
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'websearch_search_provider' not in cols:
        db.execute("ALTER TABLE settings ADD COLUMN websearch_search_provider TEXT DEFAULT ''")
    if 'websearch_search_key' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN websearch_search_key TEXT')
    if 'websearch_searxng_url' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN websearch_searxng_url TEXT')
    db.commit()


def _migrate_websearch_search_to_research(db: sqlite3.Connection) -> None:
    """Fold a web-search-tab provider config into the research one, once.

    Someone who only ever configured search under the old Web Search tab would
    otherwise find the delegate unable to search, with a working API key sitting
    in a column nothing reads any more.

    Only fills a *blank* research provider, so a user who configured both keeps
    the one they chose deliberately. Idempotent by construction: after the copy
    the research provider is non-empty, so the guard never fires again — no
    version flag needed.
    """
    row = db.execute(
        'SELECT research_search_provider, websearch_search_provider,'
        ' websearch_search_key, websearch_searxng_url FROM settings LIMIT 1'
    ).fetchone()
    if not row:
        return
    if (row['research_search_provider'] or '').strip():
        return
    provider = (row['websearch_search_provider'] or '').strip()
    if not provider:
        return
    db.execute(
        'UPDATE settings SET research_search_provider=?, research_search_key=?,'
        ' research_searxng_url=?',
        (provider, row['websearch_search_key'], row['websearch_searxng_url']),
    )
    db.commit()


def _init_fts(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS journal_fts USING fts5(
            id UNINDEXED,
            title,
            content,
            tags,
            content='journal_entries',
            content_rowid='rowid'
        )
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS journal_ai AFTER INSERT ON journal_entries BEGIN
            INSERT INTO journal_fts(rowid, id, title, content, tags)
            VALUES (NEW.rowid, NEW.id, NEW.title, NEW.content, NEW.tags);
        END
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS journal_ad AFTER DELETE ON journal_entries BEGIN
            INSERT INTO journal_fts(journal_fts, rowid, id, title, content, tags)
            VALUES ('delete', OLD.rowid, OLD.id, OLD.title, OLD.content, OLD.tags);
        END
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS journal_au AFTER UPDATE ON journal_entries BEGIN
            INSERT INTO journal_fts(journal_fts, rowid, id, title, content, tags)
            VALUES ('delete', OLD.rowid, OLD.id, OLD.title, OLD.content, OLD.tags);
            INSERT INTO journal_fts(rowid, id, title, content, tags)
            VALUES (NEW.rowid, NEW.id, NEW.title, NEW.content, NEW.tags);
        END
    """)
    db.execute("INSERT INTO journal_fts(journal_fts) VALUES('rebuild')")
    db.commit()


def _init_recipes_fts(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS recipes_fts USING fts5(
            id UNINDEXED,
            title,
            content,
            tags,
            content='recipes',
            content_rowid='rowid'
        )
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS recipes_ai AFTER INSERT ON recipes BEGIN
            INSERT INTO recipes_fts(rowid, id, title, content, tags)
            VALUES (NEW.rowid, NEW.id, NEW.title, NEW.content, NEW.tags);
        END
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS recipes_ad AFTER DELETE ON recipes BEGIN
            INSERT INTO recipes_fts(recipes_fts, rowid, id, title, content, tags)
            VALUES ('delete', OLD.rowid, OLD.id, OLD.title, OLD.content, OLD.tags);
        END
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS recipes_au AFTER UPDATE ON recipes BEGIN
            INSERT INTO recipes_fts(recipes_fts, rowid, id, title, content, tags)
            VALUES ('delete', OLD.rowid, OLD.id, OLD.title, OLD.content, OLD.tags);
            INSERT INTO recipes_fts(rowid, id, title, content, tags)
            VALUES (NEW.rowid, NEW.id, NEW.title, NEW.content, NEW.tags);
        END
    """)
    db.execute("INSERT INTO recipes_fts(recipes_fts) VALUES('rebuild')")
    db.commit()


def _init_wiki_fts(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5(
            id UNINDEXED,
            title,
            summary,
            content,
            tags,
            content='wiki_articles',
            content_rowid='rowid'
        )
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS wiki_articles_ai AFTER INSERT ON wiki_articles BEGIN
            INSERT INTO wiki_fts(rowid, id, title, summary, content, tags)
            VALUES (NEW.rowid, NEW.id, NEW.title, NEW.summary, NEW.content, NEW.tags);
        END
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS wiki_articles_ad AFTER DELETE ON wiki_articles BEGIN
            INSERT INTO wiki_fts(wiki_fts, rowid, id, title, summary, content, tags)
            VALUES ('delete', OLD.rowid, OLD.id, OLD.title, OLD.summary, OLD.content, OLD.tags);
        END
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS wiki_articles_au AFTER UPDATE ON wiki_articles BEGIN
            INSERT INTO wiki_fts(wiki_fts, rowid, id, title, summary, content, tags)
            VALUES ('delete', OLD.rowid, OLD.id, OLD.title, OLD.summary, OLD.content, OLD.tags);
            INSERT INTO wiki_fts(rowid, id, title, summary, content, tags)
            VALUES (NEW.rowid, NEW.id, NEW.title, NEW.summary, NEW.content, NEW.tags);
        END
    """)
    db.execute("INSERT INTO wiki_fts(wiki_fts) VALUES('rebuild')")
    db.commit()


def search_wiki_fts(query: str, limit: int = 20) -> list[dict]:
    """Prefix-OR search over the wiki, title-weighted.

    bm25 column weights: id, title, summary, content, tags — a title match
    should outrank a passing mention in a body paragraph.
    """
    words = [w for w in re.findall(r'\w+', query) if w]
    if not words:
        return []
    escaped = ' OR '.join(f'"{w}"*' for w in words)
    rows = get_db().execute(
        'SELECT id, bm25(wiki_fts, 0.0, 8.0, 4.0, 1.0, 2.0) AS rank'
        ' FROM wiki_fts WHERE wiki_fts MATCH ? ORDER BY rank LIMIT ?',
        (escaped, limit),
    ).fetchall()
    return [{'id': r['id'], 'rank': r['rank']} for r in rows]


def _init_fanfic_fts(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS fic_chapters_fts USING fts5(
            id UNINDEXED,
            title,
            content_text,
            content='fic_chapters',
            content_rowid='rowid'
        )
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS fic_chapters_ai AFTER INSERT ON fic_chapters BEGIN
            INSERT INTO fic_chapters_fts(rowid, id, title, content_text)
            VALUES (NEW.rowid, NEW.id, NEW.title, NEW.content_text);
        END
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS fic_chapters_ad AFTER DELETE ON fic_chapters BEGIN
            INSERT INTO fic_chapters_fts(fic_chapters_fts, rowid, id, title, content_text)
            VALUES ('delete', OLD.rowid, OLD.id, OLD.title, OLD.content_text);
        END
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS fic_chapters_au AFTER UPDATE ON fic_chapters BEGIN
            INSERT INTO fic_chapters_fts(fic_chapters_fts, rowid, id, title, content_text)
            VALUES ('delete', OLD.rowid, OLD.id, OLD.title, OLD.content_text);
            INSERT INTO fic_chapters_fts(rowid, id, title, content_text)
            VALUES (NEW.rowid, NEW.id, NEW.title, NEW.content_text);
        END
    """)
    db.execute("INSERT INTO fic_chapters_fts(fic_chapters_fts) VALUES('rebuild')")
    db.commit()


def _init_emails_fts(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
            id UNINDEXED,
            subject,
            body_text,
            sender,
            content='emails',
            content_rowid='rowid'
        )
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS emails_ai AFTER INSERT ON emails BEGIN
            INSERT INTO emails_fts(rowid, id, subject, body_text, sender)
            VALUES (NEW.rowid, NEW.id, NEW.subject, NEW.body_text, NEW.sender);
        END
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS emails_ad AFTER DELETE ON emails BEGIN
            INSERT INTO emails_fts(emails_fts, rowid, id, subject, body_text, sender)
            VALUES ('delete', OLD.rowid, OLD.id, OLD.subject, OLD.body_text, OLD.sender);
        END
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS emails_au AFTER UPDATE ON emails BEGIN
            INSERT INTO emails_fts(emails_fts, rowid, id, subject, body_text, sender)
            VALUES ('delete', OLD.rowid, OLD.id, OLD.subject, OLD.body_text, OLD.sender);
            INSERT INTO emails_fts(rowid, id, subject, body_text, sender)
            VALUES (NEW.rowid, NEW.id, NEW.subject, NEW.body_text, NEW.sender);
        END
    """)
    db.execute("INSERT INTO emails_fts(emails_fts) VALUES('rebuild')")
    db.commit()


def _drop_vector_tables(db: sqlite3.Connection) -> None:
    """Tear down the retired RAG vector store.

    Journal/recipe semantic search is gone, so nothing reads these. The plain
    tables drop unconditionally; `vec_embeddings` is a vec0 virtual table, and
    SQLite refuses to drop one whose module isn't loaded — so it only goes when
    sqlite-vec is still installed (it isn't a dependency any more). Leaving the
    declaration alone in that case is deliberate: half-dropping a virtual table
    by deleting its shadow tables leaves a wreck nothing can clean up later.

    Idempotent — after the first run there is nothing left to find.

    Note that `learning_cards.answer_embedding` is unrelated and stays; that's
    the Learning duplicate-answer hint, which never used this store.
    """
    existing = {
        r[0] for r in db.execute(
            "SELECT name FROM sqlite_master"
            " WHERE name IN ('embedding_metadata', 'embeddings', 'vec_embeddings')"
        )
    }
    if not existing:
        return
    for table in ('embedding_metadata', 'embeddings'):
        if table in existing:
            db.execute(f'DROP TABLE IF EXISTS {table}')
    if 'vec_embeddings' in existing:
        try:
            import sqlite_vec
            db.enable_load_extension(True)
            sqlite_vec.load(db)
            db.enable_load_extension(False)
            db.execute('DROP TABLE IF EXISTS vec_embeddings')
        except Exception as e:
            print(
                'Left the retired vec_embeddings table in place '
                f'(sqlite-vec unavailable: {e}). '
                'pip install sqlite-vec and restart once to drop it.'
            )
    db.commit()


def search_journal_fts(query: str, limit: int = 50) -> list[dict]:
    db = get_db()
    words = [w for w in query.split() if w]
    if not words:
        return []
    escaped = ' OR '.join(f'"{w}"*' for w in words)
    rows = db.execute(
        'SELECT id, rank FROM journal_fts WHERE journal_fts MATCH ? ORDER BY rank LIMIT ?',
        (escaped, limit),
    ).fetchall()
    return [{'id': r['id'], 'rank': r['rank']} for r in rows]


def search_recipes_fts(query: str, limit: int = 50) -> list[dict]:
    db = get_db()
    words = [w for w in query.split() if w]
    if not words:
        return []
    escaped = ' OR '.join(f'"{w}"*' for w in words)
    rows = db.execute(
        'SELECT id, rank FROM recipes_fts WHERE recipes_fts MATCH ? ORDER BY rank LIMIT ?',
        (escaped, limit),
    ).fetchall()
    return [{'id': r['id'], 'rank': r['rank']} for r in rows]


def search_emails_fts(query: str, limit: int = 50) -> list[dict]:
    db = get_db()
    words = [w for w in query.split() if w]
    if not words:
        return []
    escaped = ' OR '.join(f'"{w}"*' for w in words)
    rows = db.execute(
        'SELECT id, rank FROM emails_fts WHERE emails_fts MATCH ? ORDER BY rank LIMIT ?',
        (escaped, limit),
    ).fetchall()
    return [{'id': r['id'], 'rank': r['rank']} for r in rows]
