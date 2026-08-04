"""Shared pytest fixtures for the backend suite.

The app uses a single module-global SQLite connection (`backend.db.connection`).
The `client` fixture points that connection at a throwaway per-test database so
route handlers can be exercised end-to-end against a real (empty) schema without
touching the developer's `./data/lunaschal.db`.
"""
import os

import pytest

from backend.db import connection

# `create_app()` starts the chat-title and briefing sweeps as daemon threads that
# never stop. One app per test means two more threads per test, which exhausts the
# process partway through the suite ("Fatal Python error: Aborted"). No test needs
# them — the sweep bodies are called directly where they're under test.
os.environ.setdefault('LUNASCHAL_NO_SCHEDULERS', '1')


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
        # The research worker runs jobs on a background thread against this same
        # module-global connection, and a test that submits one can outlive its
        # own teardown — an autouse fixture draining the worker is torn down
        # *after* this one, so closing the connection here first segfaulted the
        # interpreter mid-write. Stop the work before taking its database away.
        from backend.research import worker
        worker.cancel()
        worker.wait_idle(timeout=15.0)
        if connection._conn is not None:
            connection._conn.close()
        connection._DB_PATH, connection._conn = prev_path, prev_conn
