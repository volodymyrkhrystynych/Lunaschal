"""Episodic recall: the FTS index over chat transcripts, and the read tools.

`messages` was the one substantial table in the app without a full-text index,
which is why the chat agent could not reach anything said before the current
segment — the client only ever sends the conversation since the last "New chat".
"""
import time

from ulid import ULID

from backend.db.connection import get_db
from backend.day_boundary import day_bounds, day_key_for
from backend.lifewiki import tools
from backend.lifewiki.tools import LifeTools


def _conversation(db, title='A chat', day_key=None):
    conv_id = str(ULID())
    now = int(time.time())
    db.execute(
        'INSERT INTO conversations(id, title, day_key, created_at, updated_at)'
        ' VALUES (?,?,?,?,?)',
        (conv_id, title, day_key or day_key_for(now), now, now),
    )
    db.commit()
    return conv_id


def _message(db, conv_id, content, role='user', created_at=None):
    msg_id = str(ULID())
    db.execute(
        'INSERT INTO messages(id, conversation_id, role, content, created_at)'
        ' VALUES (?,?,?,?,?)',
        (msg_id, conv_id, role, content, created_at or int(time.time())),
    )
    db.commit()
    return msg_id


# --- the index ---------------------------------------------------------


def test_the_trigger_indexes_a_message_as_it_is_written(client):
    db = get_db()
    conv = _conversation(db)
    _message(db, conv, 'The kitchen tap is dripping again')

    rows = db.execute(
        "SELECT id FROM messages_fts WHERE messages_fts MATCH '\"dripping\"*'"
    ).fetchall()
    assert len(rows) == 1


def test_editing_a_message_reindexes_it_rather_than_duplicating_it(client):
    db = get_db()
    conv = _conversation(db)
    msg = _message(db, conv, 'The kitchen tap is dripping')

    db.execute('UPDATE messages SET content=? WHERE id=?', ('The bathroom tap is dripping', msg))
    db.commit()

    assert db.execute(
        "SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH '\"dripping\"*'"
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH '\"kitchen\"*'"
    ).fetchone()[0] == 0


def test_deleting_a_message_removes_it_from_the_index(client):
    db = get_db()
    conv = _conversation(db)
    msg = _message(db, conv, 'The kitchen tap is dripping')
    db.execute('DELETE FROM messages WHERE id=?', (msg,))
    db.commit()

    assert db.execute(
        "SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH '\"dripping\"*'"
    ).fetchone()[0] == 0


def test_the_rebuild_backfills_messages_written_before_the_index_existed(client):
    """The triggers only ever see new rows. Every conversation already in the
    database predates this index, and they are the ones worth searching."""
    from backend.db.connection import _init_messages_fts

    db = get_db()
    conv = _conversation(db)
    _message(db, conv, 'Something said long ago about carburettors')
    db.execute('DROP TABLE messages_fts')
    db.commit()

    _init_messages_fts(db)

    assert db.execute(
        "SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH '\"carburettors\"*'"
    ).fetchone()[0] == 1


# --- search_conversations ----------------------------------------------


def test_search_conversations_finds_an_older_conversation(client):
    db = get_db()
    conv = _conversation(db, title='Plumbing')
    _message(db, conv, 'The kitchen tap has been dripping for a week')

    text, event = LifeTools().run_tool('search_conversations', {'query': 'dripping tap'})

    assert event['ok'] is True and event['count'] == 1
    assert 'dripping' in text
    assert 'Plumbing' in text


def test_search_conversations_excludes_the_conversation_being_had(client):
    """It is already in the transcript verbatim. Returning it again spends the
    result budget re-reading what the model can see."""
    db = get_db()
    here = _conversation(db, title='Now')
    _message(db, here, 'The tap is dripping')
    there = _conversation(db, title='Before')
    _message(db, there, 'The tap was dripping then too')

    _, event = LifeTools(here).run_tool('search_conversations', {'query': 'dripping'})
    assert event['count'] == 1

    _, event = LifeTools().run_tool('search_conversations', {'query': 'dripping'})
    assert event['count'] == 2


def test_search_conversations_says_so_when_nothing_matches(client):
    text, event = LifeTools().run_tool('search_conversations', {'query': 'carburettor'})
    assert event['ok'] is True and event['count'] == 0
    assert 'Nothing' in text


def test_a_query_of_pure_punctuation_returns_nothing_rather_than_raising(client):
    """`"` used to reach the MATCH expression unescaped and raise
    OperationalError — a 500 from typing a quote into a search box."""
    text, event = LifeTools().run_tool('search_conversations', {'query': '" ?? "'})
    assert event['ok'] is True
    assert 'No search terms' in text


def test_results_are_capped_in_total_not_per_hit(client):
    """A caller asking for ten hits of four hundred characters is the case the
    cap exists for."""
    db = get_db()
    conv = _conversation(db)
    for i in range(10):
        _message(db, conv, f'dripping tap number {i} ' + 'x' * 500)

    text, _ = LifeTools().run_tool(
        'search_conversations', {'query': 'dripping', 'limit': 10}
    )
    # The budget plus one whole hit: _join adds blocks until the next one would
    # cross the line, because half a hit reads as the whole of what was said.
    assert len(text) < tools.MAX_RESULT_CHARS + tools.MAX_HIT_CHARS + 200


def test_an_assistant_reply_is_attributed_to_the_assistant(client):
    db = get_db()
    conv = _conversation(db)
    _message(db, conv, 'You should replace the washer', role='assistant')

    text, _ = LifeTools().run_tool('search_conversations', {'query': 'washer'})
    assert 'You replied' in text


# --- search_journal ----------------------------------------------------


def test_search_journal_reaches_past_the_24_hour_window(client):
    """The system prompt carries a day of journal. This is the only path to
    anything older, and older is most of it."""
    db = get_db()
    long_ago = int(time.time()) - 60 * 60 * 24 * 90
    db.execute(
        'INSERT INTO journal_entries(id, content, title, created_at, updated_at)'
        ' VALUES (?,?,?,?,?)',
        (str(ULID()), 'Started reading about carburettors today', 'Cars', long_ago, long_ago),
    )
    db.commit()

    text, event = LifeTools().run_tool('search_journal', {'query': 'carburettors'})
    assert event['count'] == 1
    assert 'Cars' in text


# --- read_day ----------------------------------------------------------


def test_read_day_uses_the_4am_day_not_the_calendar_day(client):
    """A journal entry written at 01:00 belongs to the day the user was still
    awake in — which is the day they will ask about."""
    db = get_db()
    day = '2026-03-04'
    start, end = day_bounds(day)
    just_before_4am = end - 3600  # 03:00 the following calendar morning

    db.execute(
        'INSERT INTO journal_entries(id, content, title, created_at, updated_at)'
        ' VALUES (?,?,?,?,?)',
        (str(ULID()), 'Still up, still thinking', None, just_before_4am, just_before_4am),
    )
    db.commit()

    text, event = LifeTools().run_tool('read_day', {'date': day})
    assert event['count'] == 1
    assert 'Still up' in text


def test_read_day_gathers_the_other_sources_too(client):
    db = get_db()
    day = '2026-03-04'
    start, _ = day_bounds(day)
    noon = start + 8 * 3600

    db.execute(
        'INSERT INTO calendar_events(id, title, date, time, created_at)'
        ' VALUES (?,?,?,?,?)',
        (str(ULID()), 'Dentist', day, '14:00', start),
    )
    db.execute(
        'INSERT INTO workout_sessions(id, date, location_type, duration_minutes,'
        ' intensity_rating, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
        (str(ULID()), day, 'goodlife_alone', 45, 4, start, start),
    )
    db.execute(
        'INSERT INTO food_entries(id, dish, place, created_at, updated_at)'
        ' VALUES (?,?,?,?,?)',
        (str(ULID()), 'Ramen', 'Kinton', noon, noon),
    )
    db.commit()

    text, event = LifeTools().run_tool('read_day', {'date': day})
    assert 'Dentist' in text
    assert 'Goodlife alone' in text
    assert 'Ramen' in text
    assert event['count'] == 3


def test_read_day_rejects_something_that_is_not_a_date(client):
    text, event = LifeTools().run_tool('read_day', {'date': 'last tuesday'})
    assert event['ok'] is False
    assert 'YYYY-MM-DD' in text


def test_read_day_says_so_for_a_day_with_nothing_in_it(client):
    text, event = LifeTools().run_tool('read_day', {'date': '2026-03-04'})
    assert event['ok'] is True and event['count'] == 0
    assert 'Nothing was recorded' in text


def test_an_unknown_tool_name_is_refused_rather_than_raising(client):
    _, event = LifeTools().run_tool('search_everything', {})
    assert event['ok'] is False
