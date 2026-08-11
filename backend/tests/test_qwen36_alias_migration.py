"""The Gemma -> Qwen3.6 alias migration (_migrate_gemma_aliases_to_qwen36).

Same class of hazard as the Ollama -> llama-server migration next door, and it
exists for the same reason: a settings row can name a router alias that no
longer resolves, and *nothing anywhere validates one*. The failure is a 404 per
AI call with no obvious cause, so the migration has to catch every column that
can hold an alias, not just the obvious one.

The interesting cases are the two edges. It must clear every `gemma4*` value —
including `gemma4-vision`, which never named a real preset even before the swap
— and it must never touch a value the user picked afterwards, which is what the
marker column is for.
"""
import sqlite3

import pytest

from backend.db import connection


ALIAS_COLUMNS = ('llama_model', 'briefing_model', 'llama_vision_model', 'llama_audio_model')

SCHEMA = """
CREATE TABLE settings (
    id INTEGER PRIMARY KEY DEFAULT 1,
    llama_url TEXT DEFAULT 'http://localhost:8080',
    llama_model TEXT,
    briefing_model TEXT,
    llama_vision_model TEXT,
    llama_audio_model TEXT,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
"""


def _db(**aliases):
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    db.execute('INSERT INTO settings(id, created_at, updated_at) VALUES (1, 0, 0)')
    for col, value in aliases.items():
        db.execute(f'UPDATE settings SET {col}=? WHERE id=1', (value,))
    db.commit()
    return db


def _row(db):
    return dict(db.execute('SELECT * FROM settings WHERE id=1').fetchone())


def _columns(db):
    return {r[1] for r in db.execute('PRAGMA table_info(settings)')}


def test_clears_every_retired_gemma_alias():
    """The realistic starting state: a DB configured while Gemma was the chat
    model, with the audio and (never-working) vision toggles both ticked."""
    db = _db(
        llama_model='gemma4',
        briefing_model='gemma4-medium',
        llama_vision_model='gemma4-vision',
        llama_audio_model='gemma4-e4b-audio',
    )
    connection._migrate_gemma_aliases_to_qwen36(db)

    row = _row(db)
    for col in ALIAS_COLUMNS:
        assert row[col] is None, col


@pytest.mark.parametrize('alias', ['gemma4', 'gemma4-medium', 'gemma4-max',
                                   'gemma4-e4b-audio', 'gemma4-vision'])
def test_clears_each_retired_alias_by_name(alias):
    db = _db(llama_model=alias)
    connection._migrate_gemma_aliases_to_qwen36(db)

    assert _row(db)['llama_model'] is None


def test_leaves_the_new_omni_alias_alone():
    """`gemma4-12b-omni` still starts with 'gemma4' and is the one preset by that
    prefix that *does* exist. A prefix match that swallowed it would silently
    turn the multimodal toggle off on the first boot after enabling it."""
    db = _db(llama_vision_model='gemma4-12b-omni', llama_audio_model='gemma4-12b-omni')
    connection._migrate_gemma_aliases_to_qwen36(db)

    row = _row(db)
    assert row['llama_vision_model'] == 'gemma4-12b-omni'
    assert row['llama_audio_model'] == 'gemma4-12b-omni'


def test_leaves_unrelated_aliases_alone():
    db = _db(llama_model='qwen36', briefing_model='some-other-preset')
    connection._migrate_gemma_aliases_to_qwen36(db)

    row = _row(db)
    assert row['llama_model'] == 'qwen36'
    assert row['briefing_model'] == 'some-other-preset'


def test_is_idempotent():
    db = _db(llama_model='gemma4', llama_audio_model='gemma4-e4b-audio')
    connection._migrate_gemma_aliases_to_qwen36(db)
    first_cols, first_row = _columns(db), _row(db)

    for _ in range(3):
        connection._migrate_gemma_aliases_to_qwen36(db)

    assert _columns(db) == first_cols
    assert _row(db) == first_row


def test_does_not_clobber_a_later_user_choice():
    """It runs on every startup, so re-picking a gemma alias by hand has to
    stick. Guarded by the marker column, not by the value — the same shape
    _migrate_workout_intensity_to_stars uses."""
    db = _db(llama_model='gemma4')
    connection._migrate_gemma_aliases_to_qwen36(db)
    assert _row(db)['llama_model'] is None

    db.execute("UPDATE settings SET llama_model='gemma4' WHERE id=1")
    db.commit()
    connection._migrate_gemma_aliases_to_qwen36(db)

    assert _row(db)['llama_model'] == 'gemma4'


def test_latches_on_a_settings_table_with_no_row():
    """A DB that has never written settings still has to come out marked, or the
    migration would re-arm itself and fire on a later, legitimate choice."""
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    connection._migrate_gemma_aliases_to_qwen36(db)

    assert 'qwen36_aliases_migrated' in _columns(db)


def test_survives_a_settings_table_missing_the_optional_columns():
    """llama_vision_model / llama_audio_model are added by their own additive
    migrations. Ordering in init_db puts them first, but the guard is cheap and
    a crash here would take the whole startup down."""
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript(
        'CREATE TABLE settings (id INTEGER PRIMARY KEY DEFAULT 1, llama_model TEXT,'
        ' created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);'
        " INSERT INTO settings(id, llama_model, created_at, updated_at)"
        " VALUES (1, 'gemma4', 0, 0);"
    )
    connection._migrate_gemma_aliases_to_qwen36(db)

    assert _row(db)['llama_model'] is None
