"""The test-environment seeder has to cover every table, and stay covering it.

`scripts/seed_test_db.py` exists so a fresh checkout can be clicked through with
realistic data in every view. That promise decays silently: a new CREATE TABLE
in schema.sql just means one more screen that renders empty in the demo, and
nothing fails. So the coverage assertion is derived from `sqlite_master` rather
than from a list maintained by hand — add a table, and this test names it until
it's seeded.

The seeder guards its own env at *import* time (a bare run must never be able to
wipe ./data/lunaschal.db), so it's driven here as a subprocess with the scratch
env pointed at tmp_path, exactly the way test-env.sh drives it.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SEEDER = REPO_ROOT / 'scripts' / 'seed_test_db.py'

# Same set the seeder requires; every one is a directory under the scratch root
# except the two that name a file.
ROOT_ENV_VARS = [
    'FANFIC_ROOT', 'MEETINGS_ROOT', 'JOURNAL_ROOT', 'JOURNAL_DRAFTS_ROOT',
    'LIFESTYLE_ROOT', 'FOOD_ROOT', 'RECIPE_ROOT', 'CHAT_ROOT', 'PAPER_ROOT',
    'JOBS_ROOT', 'NEWSPAPERS_ROOT', 'NEWSPAPERS_ARCHIVE_ROOT', 'NOTEBOOK_ROOT', 'EMAIL_MEDIA_ROOT',
    'PIANO_ROOT', 'PIANO_ARCHIVE_ROOT', 'FILES_ROOT', 'TORRENT_ROOT',
    'STUDY_ROOT', 'STUDY_ARCHIVE_ROOT',
]

# Trigger-maintained FTS5 shadow tables — never seeded directly, so never
# asserted on. Matching on the `_fts` infix covers `journal_fts` itself and its
# `_data`/`_idx`/`_docsize`/`_config` shadows in one pattern.
SKIP_TABLE_SQL = "name NOT LIKE 'sqlite_%' AND name NOT LIKE '%\\_fts%' ESCAPE '\\'"


def _scratch_env(tmp_path: Path) -> dict:
    env = dict(os.environ)
    env['DATABASE_URL'] = str(tmp_path / 'seed.db')
    for var in ROOT_ENV_VARS:
        env[var] = str(tmp_path / var.lower())
    env['SHORTCUTS_PATH'] = str(tmp_path / 'shortcuts.json')
    env['LUNASCHAL_NO_SCHEDULERS'] = '1'
    return env


@pytest.fixture(scope='module')
def seeded_db(tmp_path_factory):
    """Run the seeder once for the module; yield the path it wrote."""
    pytest.importorskip('PIL', reason='seed_test_db.py needs Pillow for placeholder images')
    tmp_path = tmp_path_factory.mktemp('seed')
    env = _scratch_env(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SEEDER)],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f'seed_test_db.py failed ({result.returncode}):\n'
        f'--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}'
    )
    db_path = Path(env['DATABASE_URL'])
    assert db_path.exists(), 'seeder reported success but wrote no database'
    return db_path


def _tables(conn) -> list[str]:
    rows = conn.execute(
        f"SELECT name FROM sqlite_master WHERE type='table' AND {SKIP_TABLE_SQL} ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def test_every_table_has_at_least_one_row(seeded_db):
    conn = sqlite3.connect(seeded_db)
    try:
        tables = _tables(conn)
        # Sanity: if this ever collapses to a handful, the query is wrong and
        # the assertion below would pass vacuously.
        assert len(tables) > 90, f'expected the full schema, found {len(tables)} tables'
        empty = [
            t for t in tables
            if conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] == 0
        ]
    finally:
        conn.close()
    assert not empty, (
        f'{len(empty)} table(s) have no seeded row — add them to '
        f'scripts/seed_test_db.py:\n  ' + '\n  '.join(empty)
    )


def test_seeder_is_idempotent_by_rebuild(seeded_db, tmp_path):
    """Running it twice over the same scratch rebuilds rather than duplicating."""
    env = _scratch_env(tmp_path)
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, str(SEEDER)],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

    first = sqlite3.connect(seeded_db)
    second = sqlite3.connect(env['DATABASE_URL'])
    try:
        for table in _tables(first):
            once = first.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            twice = second.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            assert once == twice, f'{table}: {once} row(s) after one run, {twice} after two'
    finally:
        first.close()
        second.close()


def test_no_real_secrets_in_seeded_settings(seeded_db):
    """This data ships in a public repo — every credential column must be fake."""
    conn = sqlite3.connect(seeded_db)
    conn.row_factory = sqlite3.Row
    try:
        settings = conn.execute('SELECT * FROM settings WHERE id = 1').fetchone()
        assert settings is not None
        for column in ('openai_api_key', 'google_api_key', 'hf_token',
                       'research_search_key', 'adzuna_app_key'):
            value = settings[column] or ''
            assert value == '' or 'fake' in value.lower(), f'settings.{column} looks real: {value!r}'

        for row in conn.execute('SELECT cookie FROM site_cookies'):
            assert 'fake' in row['cookie'].lower()
        for row in conn.execute('SELECT access_token, imap_password FROM email_accounts'):
            for value in (row['access_token'], row['imap_password']):
                assert not value or 'fake' in value.lower()
    finally:
        conn.close()


def test_seeded_rows_survive_a_restart(seeded_db):
    """init_db()'s orphan-state resets must not rewrite anything the seeder wrote.

    Six of them run at the end of every startup (fics 'downloading', meetings
    'recording'/'transcribing', messages 'streaming', voice drafts 'processing',
    chat attachments 'running', ideas 'queued'/'running'). A row seeded in one of
    those states looks fine until the app is restarted once and it flips to error.
    """
    conn = sqlite3.connect(seeded_db)
    conn.row_factory = sqlite3.Row
    try:
        in_flight = [
            ("SELECT COUNT(*) c FROM fics WHERE download_status = 'downloading'", 'fics'),
            ("SELECT COUNT(*) c FROM meetings WHERE status IN ('recording','transcribing')", 'meetings'),
            ("SELECT COUNT(*) c FROM messages WHERE status = 'streaming'", 'messages'),
            ("SELECT COUNT(*) c FROM journal_voice_drafts WHERE status = 'processing'", 'journal_voice_drafts'),
            ("SELECT COUNT(*) c FROM chat_attachments WHERE description_status = 'running'", 'chat_attachments'),
            ("SELECT COUNT(*) c FROM ideas WHERE research_state IN ('queued','running')", 'ideas'),
        ]
        for sql, label in in_flight:
            assert conn.execute(sql).fetchone()['c'] == 0, (
                f'{label} has a row seeded in an in-flight state; init_db() will '
                'rewrite it to error on the next start'
            )
    finally:
        conn.close()
