"""Shared pytest fixtures for the backend suite.

The app uses a single module-global SQLite connection (`backend.db.connection`).
The `client` fixture points that connection at a throwaway per-test database so
route handlers can be exercised end-to-end against a real (empty) schema without
touching the developer's `./data/lunaschal.db`.
"""
import os
import shutil

import pytest

from backend.db import connection

# `create_app()` starts the chat-title and briefing sweeps as daemon threads that
# never stop. One app per test means two more threads per test, which exhausts the
# process partway through the suite ("Fatal Python error: Aborted"). No test needs
# them — the sweep bodies are called directly where they're under test.
os.environ.setdefault('LUNASCHAL_NO_SCHEDULERS', '1')


@pytest.fixture(scope='session')
def _schema_template(tmp_path_factory):
    """One initialized database, built once, copied per test.

    `init_db()` is the full schema plus every `_ensure_*` migration — about 150 ms,
    which is fine once and far too much 1,500 times.
    """
    path = tmp_path_factory.mktemp('schema') / 'template.db'
    prev_path, prev_conn = connection._DB_PATH, connection._conn
    connection._DB_PATH, connection._conn = str(path), None
    try:
        connection.init_db()
    finally:
        if connection._conn is not None:
            # Closing checkpoints the WAL back into the file, so the copy below
            # is the whole database rather than an empty shell beside a -wal.
            connection._conn.close()
        connection._DB_PATH, connection._conn = prev_path, prev_conn
    return path


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, _schema_template):
    """No test touches ./data/lunaschal.db, whether it asks for a DB or not.

    `get_db()` opens the configured path lazily and never runs the schema, so a
    test that only calls a helper — no `client` fixture in sight — used to open
    the developer's real database and quietly depend on whatever happened to be
    in it. That worked for as long as every table a helper touched already
    existed there; it stopped working the moment one didn't, and it was always
    one stray write away from being worse than a broken test.

    `client` layers its own throwaway database on top of this; the point here is
    that the ambient default is already safe.
    """
    path = tmp_path / 'ambient.db'
    shutil.copyfile(_schema_template, path)
    prev_path, prev_conn = connection._DB_PATH, connection._conn
    connection._DB_PATH, connection._conn = str(path), None
    try:
        yield
    finally:
        if connection._conn is not None:
            try:
                connection._conn.close()
            except Exception:
                pass
        connection._DB_PATH, connection._conn = prev_path, prev_conn


@pytest.fixture
def client(tmp_path):
    prev_path, prev_conn = connection._DB_PATH, connection._conn
    if prev_conn is not None:
        try:
            prev_conn.close()
        except Exception:
            pass

    # Fresh, isolated DB for this test; `init_db()` runs the schema + migrations.
    connection._DB_PATH = str(tmp_path / 'test.db')
    connection._conn = None

    from backend.app import create_app
    app = create_app()
    app.config.update(TESTING=True)
    try:
        with app.test_client() as c:
            yield c
    finally:
        # Three things run jobs on background threads against this same
        # module-global connection — the research worker, run_bg's queue
        # (journal polish, metadata, transcription, workout parsing…), and a
        # chat reply generating via backend/delegate/runs.py. A test that
        # triggers any of them can outlive its own teardown, and closing the
        # connection underneath a thread mid-query segfaults the interpreter
        # rather than raising. Stop the work before taking its database away.
        from backend.ai import background
        from backend.delegate import runs
        from backend.research import worker
        worker.cancel()
        worker.wait_idle(timeout=15.0)
        background.wait_idle(timeout=15.0)
        runs.wait_idle(timeout=15.0)
        if connection._conn is not None:
            connection._conn.close()
        connection._DB_PATH, connection._conn = prev_path, prev_conn
