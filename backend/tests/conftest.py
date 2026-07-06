"""Shared pytest fixtures for the backend suite.

The app uses a single module-global SQLite connection (`backend.db.connection`).
The `client` fixture points that connection at a throwaway per-test database so
route handlers can be exercised end-to-end against a real (empty) schema without
touching the developer's `./data/lunaschal.db`.
"""
import pytest

from backend.db import connection


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
        if connection._conn is not None:
            connection._conn.close()
        connection._DB_PATH, connection._conn = prev_path, prev_conn
