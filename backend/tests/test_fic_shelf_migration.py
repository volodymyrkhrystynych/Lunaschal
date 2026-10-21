"""The one-time shelf backfill for a library that predates shelves."""
import sqlite3

from backend.db.connection import _ensure_fic_shelf


def test_unstarted_fics_go_to_to_read_and_everything_else_to_reading():
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE fics (id TEXT PRIMARY KEY, last_opened_at INTEGER)')
    db.execute('CREATE TABLE fic_chapter_reads (chapter_id TEXT PRIMARY KEY, fic_id TEXT)')
    db.executemany('INSERT INTO fics VALUES (?,?)', [('new', None), ('opened', 5), ('read', None)])
    db.execute("INSERT INTO fic_chapter_reads VALUES ('c1', 'read')")
    _ensure_fic_shelf(db)
    shelves = dict(db.execute('SELECT id, shelf FROM fics'))
    assert shelves == {'new': 'to_read', 'opened': 'reading', 'read': 'reading'}
    db.execute("UPDATE fics SET shelf='dropped' WHERE id='new'")
    _ensure_fic_shelf(db)  # idempotent: a second start never re-shelves
    assert dict(db.execute('SELECT id, shelf FROM fics'))['new'] == 'dropped'
