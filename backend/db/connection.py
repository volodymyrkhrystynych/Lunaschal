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
    'posted_at', 'last_checked_at',
})

CAMEL_CACHE: dict[str, str] = {}


def _to_camel(s: str) -> str:
    if s not in CAMEL_CACHE:
        parts = s.split('_')
        CAMEL_CACHE[s] = parts[0] + ''.join(p.capitalize() for p in parts[1:])
    return CAMEL_CACHE[s]


def row_to_dict(row: sqlite3.Row) -> dict:
    d = {}
    for key in row.keys():
        val = row[key]
        if key in TIMESTAMP_COLS and val is not None:
            val = datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
        d[_to_camel(key)] = val
    return d


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
    _init_fts(db)
    _init_recipes_fts(db)
    _init_fanfic_fts(db)
    _init_vectors(db)
    _ensure_network_code(db)
    _ensure_writing_project_id(db)
    _ensure_stt_shortcuts(db)
    _ensure_stt_model_settings(db)
    _ensure_journal_raw_content(db)
    _ensure_flashcard_tags(db)
    _ensure_ollama_bg_model(db)
    _ensure_prevent_sleep(db)
    _ensure_todo_completed_at(db)
    _ensure_fic_review_columns(db)


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
    if 'stt_command_key' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN stt_command_key TEXT')
        db.commit()


def _ensure_stt_model_settings(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    for col in ('stt_backend', 'tts_backend', 'whisper_model'):
        if col not in cols:
            db.execute(f'ALTER TABLE settings ADD COLUMN {col} TEXT')
    if 'voice_pipeline_enabled' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN voice_pipeline_enabled INTEGER DEFAULT 1')
    db.commit()


def _ensure_ollama_bg_model(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'ollama_bg_model' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN ollama_bg_model TEXT')
        db.commit()


def _ensure_todo_completed_at(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(todos)')}
    if 'completed_at' not in cols:
        db.execute('ALTER TABLE todos ADD COLUMN completed_at INTEGER')
        # Best guess for todos completed before this column existed: their
        # last update was the moment they were checked off.
        db.execute('UPDATE todos SET completed_at=updated_at WHERE done=1')
        db.commit()


def _ensure_fic_review_columns(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(fics)')}
    if 'rating' not in cols:
        db.execute('ALTER TABLE fics ADD COLUMN rating INTEGER CHECK(rating BETWEEN 1 AND 5)')
    if 'review' not in cols:
        db.execute('ALTER TABLE fics ADD COLUMN review TEXT')
    db.commit()


def _ensure_prevent_sleep(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
    if 'prevent_sleep' not in cols:
        db.execute('ALTER TABLE settings ADD COLUMN prevent_sleep INTEGER DEFAULT 0')
        db.commit()


def _ensure_journal_raw_content(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(journal_entries)')}
    if 'raw_content' not in cols:
        db.execute('ALTER TABLE journal_entries ADD COLUMN raw_content TEXT')
        db.commit()


def _ensure_flashcard_tags(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(flashcards)')}
    if 'tags' not in cols:
        db.execute('ALTER TABLE flashcards ADD COLUMN tags TEXT')
        db.commit()


def _ensure_writing_project_id(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute('PRAGMA table_info(conversations)')}
    if 'writing_project_id' not in cols:
        db.execute('ALTER TABLE conversations ADD COLUMN writing_project_id TEXT REFERENCES writing_projects(id)')
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


def search_fanfic_fts(query: str, limit: int = 50) -> list[dict]:
    db = get_db()
    words = [w for w in query.split() if w]
    if not words:
        return []
    escaped = ' OR '.join(f'"{w}"*' for w in words)
    rows = db.execute(
        'SELECT id, rank FROM fic_chapters_fts WHERE fic_chapters_fts MATCH ? ORDER BY rank LIMIT ?',
        (escaped, limit),
    ).fetchall()
    return [{'id': r['id'], 'rank': r['rank']} for r in rows]
