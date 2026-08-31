"""The window digest: what the nightly pass is shown, and nothing else.

Pure with respect to the model, so the whole of its input can be asserted
without a generation. The citation ids are the load-bearing detail — a model
that was never shown an id cannot produce one, and a fact without one is dropped.
"""
import time

from ulid import ULID

from backend.db.connection import get_db
from backend.day_boundary import day_bounds
from backend.lifewiki import digest

DAY = '2026-03-04'


def _at(offset_hours=8):
    return day_bounds(DAY)[0] + offset_hours * 3600


def test_every_line_carries_the_id_of_its_source_row(client):
    """The single most load-bearing detail in the module."""
    db = get_db()
    entry_id = str(ULID())
    db.execute(
        'INSERT INTO journal_entries(id, content, created_at, updated_at)'
        ' VALUES (?,?,?,?)',
        (entry_id, 'Slept badly', _at(), _at()),
    )
    db.commit()

    text = digest.render(digest.gather(DAY, 1))
    assert f'[journal:{entry_id}]' in text


def test_it_gathers_all_six_sources(client):
    db = get_db()
    ids = {k: str(ULID()) for k in
           ('journal', 'message', 'food', 'workout', 'calendar', 'conv')}
    db.execute('INSERT INTO journal_entries(id, content, created_at, updated_at)'
               ' VALUES (?,?,?,?)', (ids['journal'], 'Wrote something', _at(), _at()))
    db.execute('INSERT INTO conversations(id, created_at, updated_at) VALUES (?,?,?)',
               (ids['conv'], _at(), _at()))
    db.execute("INSERT INTO messages(id, conversation_id, role, content, created_at)"
               " VALUES (?,?,'user',?,?)",
               (ids['message'], ids['conv'], 'I keep forgetting to stretch', _at()))
    db.execute('INSERT INTO food_entries(id, dish, place, created_at, updated_at)'
               ' VALUES (?,?,?,?,?)', (ids['food'], 'Ramen', 'Kinton', _at(), _at()))
    db.execute('INSERT INTO workout_sessions(id, date, location_type, created_at,'
               ' updated_at) VALUES (?,?,?,?,?)',
               (ids['workout'], DAY, 'goodlife_alone', _at(), _at()))
    db.execute('INSERT INTO calendar_events(id, title, date, created_at)'
               ' VALUES (?,?,?,?)', (ids['calendar'], 'Dentist', DAY, _at()))
    db.commit()

    text = digest.render(digest.gather(DAY, 1))
    for key in ('journal', 'message', 'food', 'workout', 'calendar'):
        assert f'[{key}:{ids[key]}]' in text, key


def test_the_assistants_own_replies_are_never_shown(client):
    """Building a standing fact from its own prose is the shortest path to a
    confident invention — it would be learning from itself."""
    db = get_db()
    conv = str(ULID())
    db.execute('INSERT INTO conversations(id, created_at, updated_at) VALUES (?,?,?)',
               (conv, _at(), _at()))
    db.execute("INSERT INTO messages(id, conversation_id, role, content, created_at)"
               " VALUES (?,?,'assistant',?,?)",
               (str(ULID()), conv, 'You seem to love ramen', _at()))
    db.commit()

    assert 'love ramen' not in digest.render(digest.gather(DAY, 1))


def test_pending_observations_ride_along(client):
    from backend import observations

    noted = observations.add_observation('Trains on Tuesdays')
    text = digest.render(digest.gather(DAY, 1))
    assert f'[observation:{noted["id"]}]' in text


def test_a_quiet_window_is_empty_rather_than_a_page_of_headings(client):
    window = digest.gather(DAY, 1)
    assert digest.is_empty(window)
    assert digest.render(window) == ''


def test_the_window_covers_whole_4am_days(client):
    """A journal entry written at 01:00 belongs to the day the user was still
    awake in."""
    db = get_db()
    start, end = day_bounds(DAY)
    db.execute('INSERT INTO journal_entries(id, content, created_at, updated_at)'
               ' VALUES (?,?,?,?)', (str(ULID()), 'Late night', end - 3600, end - 3600))
    db.execute('INSERT INTO journal_entries(id, content, created_at, updated_at)'
               ' VALUES (?,?,?,?)', (str(ULID()), 'Too early', start - 3600, start - 3600))
    db.commit()

    text = digest.render(digest.gather(DAY, 1))
    assert 'Late night' in text
    assert 'Too early' not in text


def test_a_multi_day_window_reaches_back(client):
    db = get_db()
    earlier = day_bounds('2026-03-02')[0] + 3600
    db.execute('INSERT INTO journal_entries(id, content, created_at, updated_at)'
               ' VALUES (?,?,?,?)', (str(ULID()), 'Two days ago', earlier, earlier))
    db.commit()

    assert 'Two days ago' not in digest.render(digest.gather(DAY, 1))
    assert 'Two days ago' in digest.render(digest.gather(DAY, 3))


def test_each_source_is_capped_on_its_own(client):
    """Bounded per source rather than in total, so one talkative day of chat
    cannot crowd out a week of workouts."""
    db = get_db()
    for i in range(digest.MAX_JOURNAL + 5):
        db.execute('INSERT INTO journal_entries(id, content, created_at, updated_at)'
                   ' VALUES (?,?,?,?)',
                   (str(ULID()), f'Entry {i}', _at() + i, _at() + i))
    db.commit()

    window = digest.gather(DAY, 1)
    assert len(window['sources']['journal']) == digest.MAX_JOURNAL


def test_a_long_entry_is_clipped(client):
    db = get_db()
    db.execute('INSERT INTO journal_entries(id, content, created_at, updated_at)'
               ' VALUES (?,?,?,?)', (str(ULID()), 'x' * 5000, _at(), _at()))
    db.commit()

    line = digest.gather(DAY, 1)['sources']['journal'][0]
    assert len(line) < digest.MAX_ITEM_CHARS + 200


# --- for_sources, what a rebuild reads ---------------------------------


def test_for_sources_reads_exactly_the_rows_it_is_given(client):
    db = get_db()
    wanted, other = str(ULID()), str(ULID())
    for entry_id, text in ((wanted, 'The one cited'), (other, 'Not cited')):
        db.execute('INSERT INTO journal_entries(id, content, created_at, updated_at)'
                   ' VALUES (?,?,?,?)', (entry_id, text, _at(), _at()))
    db.commit()

    text = digest.render(digest.for_sources([('journal', wanted)]))
    assert 'The one cited' in text
    assert 'Not cited' not in text


def test_for_sources_survives_a_source_row_that_is_gone(client):
    """A deleted journal entry, or a folded observation. A rebuild keeps only
    what it can still verify."""
    window = digest.for_sources([('journal', 'gone'), ('observation', 'also-gone')])
    assert digest.is_empty(window)
