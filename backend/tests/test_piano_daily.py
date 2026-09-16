"""The daily routine's grouping, mastery streaks and completion gate."""
import time

from ulid import ULID

from backend.db.connection import get_db
from backend.piano import daily


def _exercise(db, *, exercise_key='five-finger', minutes=5, day_key='2026-09-15'):
    daily_id = str(ULID())
    db.execute(
        'INSERT INTO piano_daily_exercises '
        '(id,day_key,exercise_key,position,key_name,target_tempo,minutes,created_at) '
        'VALUES (?,?,?,0,?,?,?,?)',
        (daily_id, day_key, exercise_key, 'C', 80, minutes, int(time.time())),
    )
    db.commit()
    return daily_id


def _attempt(db, daily_id, *, wrong_notes, seconds=60):
    now = int(time.time())
    daily.record_attempt(db, daily_id, {
        'startedAt': now - seconds,
        'tempo': 80,
        'correctNotes': 9,
        'wrongNotes': wrong_notes,
        'complete': False,
    })


def test_every_exercise_belongs_to_one_of_the_day_s_blocks(client):
    groups = {item.key: item.group for item in daily.EXERCISES}
    assert groups == {
        'five-finger': 'keys',
        'scales': 'keys',
        'classical-cadence': 'keys',
        'articulation': 'keys',
        'ii-v-i': 'keys',
        'ear-phrase': 'ear',
        'sight-reading': 'freeform',
        'comping': 'freeform',
        'guide-tone-solo': 'freeform',
    }
    # Only the exercises the falling notes can drive are drilled to mastery.
    assert all(
        daily.BY_KEY[key].notation and daily.BY_KEY[key].gradeable
        for key, group in groups.items() if group == 'keys'
    )


def test_serialized_routine_carries_its_group(client):
    db = get_db()
    _exercise(db, exercise_key='five-finger')
    _exercise(db, exercise_key='ear-phrase')
    piece_id = str(ULID())
    db.execute(
        'INSERT INTO piano_pieces (id,title,composer,source_filename,score_path,'
        'created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
        (piece_id, 'Gymnopedie', 'Satie', 'g.musicxml', '/tmp/g.musicxml',
         int(time.time()), int(time.time())),
    )
    db.execute(
        'INSERT INTO piano_daily_exercises '
        '(id,day_key,exercise_key,position,minutes,piano_piece_id,created_at) '
        'VALUES (?,?,?,?,?,?,?)',
        (str(ULID()), '2026-09-15', 'repertoire', 2, 6, piece_id, int(time.time())),
    )
    db.commit()

    rows = db.execute(
        'SELECT * FROM piano_daily_exercises WHERE day_key=? ORDER BY position',
        ('2026-09-15',),
    ).fetchall()
    assert [daily._serialize(db, row)['group'] for row in rows] == [
        'keys', 'ear', 'repertoire',
    ]


def test_a_drill_run_is_banked_without_finishing_the_exercise(client):
    db = get_db()
    daily_id = _exercise(db)

    _attempt(db, daily_id, wrong_notes=2)
    row = db.execute(
        'SELECT completed_at FROM piano_daily_exercises WHERE id=?', (daily_id,)
    ).fetchone()
    assert row['completed_at'] is None
    assert db.execute(
        'SELECT COUNT(*) c FROM piano_exercise_attempts WHERE daily_exercise_id=?',
        (daily_id,),
    ).fetchone()['c'] == 1

    # The default is still to complete, which is what the self-rated cards send.
    daily.record_attempt(db, daily_id, {'selfRating': 4})
    assert db.execute(
        'SELECT completed_at FROM piano_daily_exercises WHERE id=?', (daily_id,)
    ).fetchone()['completed_at'] is not None


def test_clean_streak_counts_back_from_the_newest_run(client):
    db = get_db()
    daily_id = _exercise(db)

    _attempt(db, daily_id, wrong_notes=0)
    _attempt(db, daily_id, wrong_notes=0)
    row = db.execute('SELECT * FROM piano_daily_exercises WHERE id=?', (daily_id,)).fetchone()
    assert daily._serialize(db, row)['cleanStreak'] == 2

    _attempt(db, daily_id, wrong_notes=0)
    assert daily._serialize(db, row)['cleanStreak'] == daily.CLEAN_RUNS_REQUIRED

    # A fourth run is still capped, and one wrong note wipes the streak out.
    _attempt(db, daily_id, wrong_notes=0)
    assert daily._serialize(db, row)['cleanStreak'] == daily.CLEAN_RUNS_REQUIRED
    _attempt(db, daily_id, wrong_notes=1)
    assert daily._serialize(db, row)['cleanStreak'] == 0


def test_self_rated_attempts_neither_extend_nor_break_a_streak(client):
    db = get_db()
    daily_id = _exercise(db)
    _attempt(db, daily_id, wrong_notes=0)
    daily.record_attempt(db, daily_id, {'selfRating': 3, 'complete': False})

    row = db.execute('SELECT * FROM piano_daily_exercises WHERE id=?', (daily_id,)).fetchone()
    assert daily._serialize(db, row)['cleanStreak'] == 1


def test_practiced_seconds_sums_the_runs_so_the_budget_survives_an_exit(client):
    db = get_db()
    daily_id = _exercise(db, minutes=5)
    _attempt(db, daily_id, wrong_notes=1, seconds=70)
    _attempt(db, daily_id, wrong_notes=0, seconds=50)

    row = db.execute('SELECT * FROM piano_daily_exercises WHERE id=?', (daily_id,)).fetchone()
    assert daily._serialize(db, row)['practicedSeconds'] == 120


def test_a_run_with_no_start_time_contributes_nothing_to_the_budget(client):
    db = get_db()
    daily_id = _exercise(db)
    daily.record_attempt(db, daily_id, {'wrongNotes': 0, 'complete': False})

    row = db.execute('SELECT * FROM piano_daily_exercises WHERE id=?', (daily_id,)).fetchone()
    assert daily._serialize(db, row)['practicedSeconds'] == 0
