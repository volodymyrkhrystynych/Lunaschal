"""The Ollama -> llama-server settings migration (_ensure_llama_server_settings).

This one is worth pinning down because it is the only migration in the project
that *drops* columns and rewrites values rather than just appending, and it runs
against the user's real DB on the next startup. Two things it must get right:

- The old Ollama model tag must NOT be carried forward. 'qwen3.6:35b' is not a
  llama-server router alias, and silently keeping it would 404 every AI call with
  no obvious cause.
- Re-running it must be a no-op. The two older migrations that used to own the
  graded reasoning_effort columns no longer create them, so nothing should
  resurrect a column this one drops — otherwise every startup would churn the
  schema.
"""
import sqlite3

import pytest

from backend.db import connection


# The settings table as it was before the migration, with the columns this
# migration reads or removes.
LEGACY_SCHEMA = """
CREATE TABLE settings (
    id INTEGER PRIMARY KEY DEFAULT 1,
    ai_provider TEXT DEFAULT 'openai',
    ai_model TEXT,
    openai_api_key TEXT,
    google_api_key TEXT,
    ollama_url TEXT DEFAULT 'http://localhost:11434',
    ollama_model TEXT,
    network_code TEXT,
    stt_backend TEXT, tts_backend TEXT, whisper_model TEXT, stt_device TEXT,
    voice_pipeline_enabled INTEGER DEFAULT 1,
    llm_reasoning_effort TEXT DEFAULT 'none',
    briefing_reasoning_effort TEXT DEFAULT 'none',
    llm_max_tokens INTEGER DEFAULT 4096,
    briefing_model TEXT,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
"""

DEAD_COLUMNS = {
    'ollama_url', 'ollama_model',
    'llm_reasoning_effort', 'briefing_reasoning_effort',
}


def _legacy_db(llm_effort='none', briefing_effort='none', model='qwen3.6:35b',
               briefing_model='qwen3.6:35b'):
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript(LEGACY_SCHEMA)
    db.execute(
        'INSERT INTO settings(id, ollama_url, ollama_model, llm_reasoning_effort,'
        ' briefing_reasoning_effort, briefing_model, created_at, updated_at)'
        " VALUES (1, 'http://localhost:11434', ?, ?, ?, ?, 0, 0)",
        (model, llm_effort, briefing_effort, briefing_model),
    )
    db.commit()
    return db


def _columns(db):
    return {r[1] for r in db.execute('PRAGMA table_info(settings)')}


def _row(db):
    return dict(db.execute('SELECT * FROM settings WHERE id=1').fetchone())


def test_replaces_ollama_columns_with_llama_ones():
    db = _legacy_db()
    connection._ensure_llama_server_settings(db)

    assert not DEAD_COLUMNS & _columns(db)
    row = _row(db)
    assert row['llama_url'] == 'http://localhost:8080'
    # Deliberately not carried forward — an Ollama tag is not a router alias, and
    # a NULL here means "whatever the router loads by default".
    assert row['llama_model'] is None


def test_clears_the_stale_briefing_model_override():
    """briefing_model holds the same kind of value in its own column, so it needs
    the same treatment. Missing it was a real bug: the briefing would 404 on
    'qwen3.6:35b' while interactive chat kept working fine."""
    db = _legacy_db(briefing_model='qwen3.6:35b')
    connection._ensure_llama_server_settings(db)

    assert _row(db)['briefing_model'] is None


def test_keeps_a_briefing_model_the_user_picks_afterwards():
    db = _legacy_db()
    connection._ensure_llama_server_settings(db)

    db.execute("UPDATE settings SET briefing_model='gemma4-long' WHERE id=1")
    db.commit()
    connection._ensure_llama_server_settings(db)

    assert _row(db)['briefing_model'] == 'gemma4-long'


@pytest.mark.parametrize('effort,expected', [
    ('none', 0), (None, 0),
    ('low', 1), ('medium', 1), ('high', 1), ('max', 1),
])
def test_briefing_keeps_its_thinking_intent(effort, expected):
    """Gemma 4 has one thinking channel, so any non-'none' level means "on". The
    briefing runs overnight, so carrying the old intent over costs nothing."""
    db = _legacy_db(briefing_effort=effort)
    connection._ensure_llama_server_settings(db)

    assert _row(db)['briefing_thinking'] == expected


@pytest.mark.parametrize('effort', ['none', None, 'low', 'medium', 'high', 'max'])
def test_chat_thinking_always_resets_to_off(effort):
    """The one place the migration deliberately discards the old setting.

    A graded level bounded how much the previous model thought; Gemma 4's channel
    is unbounded, and measured on this hardware it turns a 1.3s first token into
    25s. Someone who had 'high' asked for more reasoning, not for a 20x latency
    regression, so chat starts off and Settings makes it one click to re-enable.
    """
    db = _legacy_db(llm_effort=effort)
    connection._ensure_llama_server_settings(db)

    assert _row(db)['llm_thinking'] == 0


def test_is_idempotent():
    db = _legacy_db(llm_effort='high', briefing_effort='high')
    connection._ensure_llama_server_settings(db)
    first_cols, first_row = _columns(db), _row(db)

    for _ in range(3):
        connection._ensure_llama_server_settings(db)

    assert _columns(db) == first_cols
    assert _row(db) == first_row


def test_does_not_clobber_a_later_user_choice():
    """The migration runs on *every* startup, so it must not fight the user: once
    they turn chat thinking back on in Settings, the next boot has to leave it on.
    Guarded by the `new in cols` early return rather than by the UPDATE."""
    db = _legacy_db(llm_effort='high')
    connection._ensure_llama_server_settings(db)
    assert _row(db)['llm_thinking'] == 0  # reset, as designed

    db.execute('UPDATE settings SET llm_thinking=1 WHERE id=1')
    db.commit()
    connection._ensure_llama_server_settings(db)

    assert _row(db)['llm_thinking'] == 1


def test_runs_cleanly_on_a_fresh_schema():
    """A brand-new DB gets llama_url/llama_model straight from schema.sql, so the
    migration has nothing to add and no old columns to read."""
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript(
        'CREATE TABLE settings (id INTEGER PRIMARY KEY DEFAULT 1,'
        " llama_url TEXT DEFAULT 'http://localhost:8080', llama_model TEXT,"
        ' created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);'
        ' INSERT INTO settings(id, created_at, updated_at) VALUES (1, 0, 0);'
    )
    connection._ensure_llama_server_settings(db)

    cols = _columns(db)
    assert {'llama_url', 'llama_model', 'llm_thinking', 'briefing_thinking'} <= cols
    assert not DEAD_COLUMNS & cols
    assert _row(db)['llm_thinking'] == 0
