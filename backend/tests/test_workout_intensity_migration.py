"""The 1-10 RPE -> 1-5 star migration (_migrate_workout_intensity_to_stars).

Worth pinning down because it rewrites values in place on the user's real DB at
the next startup, and because it is *not* idempotent by nature: the fold
ceil(v/2) is only correct on un-migrated data. A "convert everything above 5"
heuristic would leave a genuine old 4 (light work) reading as 4 stars ("I'm
really trying hard"), and a second unguarded run would crush a real 4 down to 2.
So the marker column carries the whole guarantee.
"""
import sqlite3

import pytest

from backend.db import connection

SCHEMA = """
CREATE TABLE settings (
    id INTEGER PRIMARY KEY DEFAULT 1,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE workout_sessions (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    location_type TEXT NOT NULL,
    duration_minutes INTEGER,
    intensity_rating INTEGER,
    raw_text TEXT,
    notes TEXT,
    parse_status TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
"""


def _db(ratings, with_settings_row=True):
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    if with_settings_row:
        db.execute('INSERT INTO settings(id, created_at, updated_at) VALUES (1, 0, 0)')
    for i, rating in enumerate(ratings):
        db.execute(
            'INSERT INTO workout_sessions(id, date, location_type, intensity_rating,'
            ' created_at, updated_at) VALUES (?, ?, ?, ?, 0, 0)',
            (f's{i}', '2026-07-20', 'outside', rating),
        )
    db.commit()
    return db


def _ratings(db):
    return [
        r['intensity_rating']
        for r in db.execute('SELECT intensity_rating FROM workout_sessions ORDER BY id')
    ]


def _columns(db):
    return {r[1] for r in db.execute('PRAGMA table_info(settings)')}


@pytest.mark.parametrize('old,new', [
    (1, 1), (2, 1),   # "not intense whatsoever"
    (3, 2), (4, 2),   # "just a smidge"
    (5, 3), (6, 3),   # "I'm sweating"
    (7, 4), (8, 4),   # "I'm really trying hard"
    (9, 5), (10, 5),  # "I am going ham"
])
def test_folds_the_ten_point_scale_onto_five_stars(old, new):
    db = _db([old])
    connection._migrate_workout_intensity_to_stars(db)
    assert _ratings(db) == [new]


def test_leaves_unrated_sessions_alone():
    db = _db([None, 7, None])
    connection._migrate_workout_intensity_to_stars(db)
    assert _ratings(db) == [None, 4, None]


def test_running_it_again_does_not_fold_a_second_time():
    """The whole point of the marker: a 7/10 becomes 4 stars once and stays
    there, instead of decaying to 2 on the next startup."""
    db = _db([7, 10, 1])
    connection._migrate_workout_intensity_to_stars(db)
    once = _ratings(db)
    assert once == [4, 5, 1]

    for _ in range(3):
        connection._migrate_workout_intensity_to_stars(db)
    assert _ratings(db) == once


def test_marker_column_is_added_and_set():
    db = _db([7])
    assert 'workout_intensity_five_star' not in _columns(db)
    connection._migrate_workout_intensity_to_stars(db)
    assert 'workout_intensity_five_star' in _columns(db)
    assert db.execute(
        'SELECT workout_intensity_five_star FROM settings WHERE id=1'
    ).fetchone()[0] == 1


def test_marker_latches_even_with_no_settings_row():
    """A fresh DB may have no settings row yet; the column's *existence* is the
    marker, so the migration must still be one-shot."""
    db = _db([9], with_settings_row=False)
    connection._migrate_workout_intensity_to_stars(db)
    connection._migrate_workout_intensity_to_stars(db)
    assert _ratings(db) == [5]


def test_a_fresh_database_is_untouched_and_marked():
    """New installs have nothing to fold, but must still be marked so a later
    5-star value is never mistaken for an un-migrated RPE."""
    db = _db([])
    connection._migrate_workout_intensity_to_stars(db)
    assert 'workout_intensity_five_star' in _columns(db)

    db.execute(
        'INSERT INTO workout_sessions(id, date, location_type, intensity_rating,'
        " created_at, updated_at) VALUES ('new', '2026-07-30', 'outside', 4, 0, 0)"
    )
    db.commit()
    connection._migrate_workout_intensity_to_stars(db)
    assert _ratings(db) == [4]


def test_skips_a_database_without_the_workouts_table():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript(
        'CREATE TABLE settings (id INTEGER PRIMARY KEY DEFAULT 1,'
        ' created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);'
    )
    connection._migrate_workout_intensity_to_stars(db)
    assert 'workout_intensity_five_star' not in _columns(db)


def test_runs_on_startup(client):
    """Wired into init_db, not just defined — otherwise old rows never fold."""
    from backend.db.connection import get_db
    assert 'workout_intensity_five_star' in {
        r[1] for r in get_db().execute('PRAGMA table_info(settings)')
    }
