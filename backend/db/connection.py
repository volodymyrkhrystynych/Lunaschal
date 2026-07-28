import os
import random
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

_DB_PATH = os.environ.get('DATABASE_URL', './data/lunaschal.db')
_conn: sqlite3.Connection | None = None

TIMESTAMP_COLS = frozenset({
    'created_at', 'updated_at', 'next_review', 'completed_at',
    'posted_at', 'last_checked_at', 'started_at', 'ended_at', 'due',
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
    _init_fanfic_fts(db)
    _init_vectors(db)
    _ensure_network_code(db)
    _ensure_writing_project_id(db)
    _ensure_conversation_day_key(db)
    _ensure_stt_shortcuts(db)
    _ensure_stt_model_settings(db)
    _ensure_journal_raw_content(db)
    _migrate_flashcards_to_learning(db)
    _ensure_prevent_sleep(db)
    _ensure_nudge_settings(db)
    _ensure_briefing_settings(db)
    _ensure_llm_generation_settings(db)
    _ensure_todo_completed_at(db)
    _ensure_todo_list_columns(db)
    _ensure_todo_priority(db)
    _ensure_task_event_columns(db)
    _ensure_calendar_recurrence(db)
    _ensure_fic_review_columns(db)
    _ensure_fic_folder_position(db)
    _ensure_fic_update_pending(db)
    _repair_escaped_image_fallbacks(db)
    _ensure_paper_archive_requested(db)
    _ensure_food_location(db)
    _ensure_hf_token(db)
    _ensure_meeting_speaker_names(db)
    _ensure_meeting_echo_cancel(db)
    _ensure_meeting_source(db)
    _ensure_meeting_pause_columns(db)
    _ensure_meeting_whisper_columns(db)
    _reset_stale_fic_downloads(db)
    _reset_stale_meetings(db)


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


def _ensure_calendar_recurrence(db: sqlite3.Connection) -> None:
    """Recurrence rule columns on calendar_events.

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
        # No REFERENCES here: SQLite can't add an FK constraint to an existing
        # table via ALTER, and the column is a breadcrumb, not an invariant.
        ('split_from', 'TEXT'),
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


def _ensure_fic_update_pending(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(fics)')}
    if 'update_pending' not in cols:
        db.execute('ALTER TABLE fics ADD COLUMN update_pending INTEGER NOT NULL DEFAULT 0')
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
    if 'briefing_reasoning_effort' not in cols:
        # How hard the briefing model "thinks" before answering: one of
        # none/low/medium/high/max (Ollama's reasoning_effort levels). 'none' by
        # default — reasoning models otherwise spend their whole output budget on
        # chain-of-thought, leaving the JSON empty/truncated.
        db.execute(
            "ALTER TABLE settings ADD COLUMN briefing_reasoning_effort TEXT DEFAULT 'none'"
        )
    if 'briefing_max_tokens' not in cols:
        # Output-token ceiling for the briefing completion. The briefing runs
        # overnight, so this is deliberately generous.
        db.execute('ALTER TABLE settings ADD COLUMN briefing_max_tokens INTEGER DEFAULT 16384')
    if 'briefing_num_ctx' not in cols:
        # Context window for the briefing. Deliberately the same as the chat
        # default: the briefing usually shares the chat model, and changing
        # num_ctx between requests makes Ollama re-allocate the KV cache and
        # reload the weights. Keep the two in step when tuning either.
        db.execute('ALTER TABLE settings ADD COLUMN briefing_num_ctx INTEGER DEFAULT 8192')
    db.commit()


def _ensure_llm_generation_settings(db: sqlite3.Connection) -> None:
    """Reasoning level + output ceiling applied to the default (conversational)
    model — the one the user chats with. Kept deliberately simple: one standard
    pair that applies the same way to every model, thinking or not."""
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'llm_reasoning_effort' not in cols:
        db.execute(
            "ALTER TABLE settings ADD COLUMN llm_reasoning_effort TEXT DEFAULT 'none'"
        )
    if 'llm_max_tokens' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN llm_max_tokens INTEGER DEFAULT 4096')
    if 'llm_num_ctx' not in cols:
        # Context window (num_ctx). Ollama's own default is 4096; 8192 gives a
        # thinking model room for prompt + reasoning + answer together. Raise it
        # for a long-context model, but cost scales with the model's KV shape,
        # not its size — see docs/learnings/local-model-context-budget.md.
        db.execute('ALTER TABLE settings ADD COLUMN llm_num_ctx INTEGER DEFAULT 8192')
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


def _init_vectors(db: sqlite3.Connection) -> None:
    try:
        import sqlite_vec
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        db.enable_load_extension(False)
        db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_embeddings USING vec0(
                id TEXT PRIMARY KEY,
                embedding FLOAT[1536]
            )
        """)
        db.commit()
    except Exception as e:
        print(f'Vector store init skipped: {e}')


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
