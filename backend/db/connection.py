import os
import random
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

_DB_PATH = os.environ.get('DATABASE_URL', './data/lunaschal.db')
_conn: sqlite3.Connection | None = None

TIMESTAMP_COLS = frozenset({
    'created_at', 'updated_at', 'next_review',
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
    _init_vectors(db)
    _ensure_network_code(db)
    _ensure_writing_project_id(db)
    _ensure_stt_shortcuts(db)


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
