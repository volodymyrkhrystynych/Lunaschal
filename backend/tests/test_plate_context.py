"""The "today" block, and the order the system prompt's blocks are assembled in.

The 05:00 briefing has read the user's to-dos, daily tasks and cards due from
these tables for months (backend/ai/briefing.py's gather_briefing_context). The
assistant they actually talk to could not see any of it — it could write into
`chat_todos` via add_todos and then had no idea what was in there.
"""
import time

from ulid import ULID

from backend.ai.chat import build_chat_system_prompt, format_plate_context
from backend.db.connection import get_db
from backend.day_boundary import day_key_for


def _chat_todo(db, title, done=0):
    now = int(time.time())
    db.execute(
        'INSERT INTO chat_todos(id, day_key, title, done, created_at, updated_at)'
        ' VALUES (?,?,?,?,?,?)',
        (str(ULID()), day_key_for(now), title, done, now, now),
    )
    db.commit()


def _todo(db, title, priority=3):
    now = int(time.time())
    db.execute(
        "INSERT INTO todos(id, title, list, done, priority, created_at, updated_at)"
        " VALUES (?,?,'todo',0,?,?,?)",
        (str(ULID()), title, priority, now, now),
    )
    db.commit()


def test_an_empty_day_renders_no_block_at_all(client):
    """Every other context block returns '' when it has nothing. A heading with
    nothing under it costs tokens twice a turn and reads as an empty life."""
    assert format_plate_context() == ''


def test_todays_bar_is_visible_with_what_is_already_done(client):
    db = get_db()
    _chat_todo(db, 'Call the dentist')
    _chat_todo(db, 'Book the car in', done=1)

    block = format_plate_context()
    assert 'Call the dentist — not done' in block
    assert 'Book the car in — done' in block


def test_open_todos_appear_with_their_priority(client):
    db = get_db()
    _todo(db, 'Renew the passport', priority=5)

    block = format_plate_context()
    assert 'Renew the passport' in block
    assert 'priority 5/5' in block


def test_the_todo_list_is_cut_rather_than_dumped(client):
    """Past a handful it stops being "what is on your plate" and becomes a
    backlog the model reads as uniformly urgent."""
    from backend.ai.chat import PLATE_MAX_TODOS

    db = get_db()
    for i in range(PLATE_MAX_TODOS + 4):
        _todo(db, f'Thing {i}')

    block = format_plate_context()
    assert block.count('- Thing') == PLATE_MAX_TODOS
    assert 'and 4 more' in block


def test_a_finished_day_still_renders_its_bar(client):
    """Everything ticked is information — it is the difference between a quiet
    day and a day that has not started."""
    db = get_db()
    _chat_todo(db, 'Call the dentist', done=1)
    assert 'Call the dentist — done' in format_plate_context()


# --- ordering ----------------------------------------------------------


def test_prompt_blocks_run_least_to_most_volatile(client):
    """The prompt is paid twice per turn and llama-server's prefix cache
    survives only up to the first block that changed. The memory document is the
    same tomorrow; the plate changes when a to-do is ticked. Reversing these two
    would re-prefill everything under the plate on every tick.
    """
    from backend import memory, observations

    db = get_db()
    memory.set_memory('- Their gym is Movati', source='user')
    observations.add_observation('Trains on Tuesdays')
    db.execute(
        'INSERT INTO journal_entries(id, content, created_at, updated_at)'
        ' VALUES (?,?,?,?)',
        (str(ULID()), 'A quiet morning', int(time.time()), int(time.time())),
    )
    db.commit()
    _chat_todo(db, 'Call the dentist')

    prompt = build_chat_system_prompt()
    assert (prompt.index('Movati')
            < prompt.index('Trains on Tuesdays')
            < prompt.index('A quiet morning')
            < prompt.index('Call the dentist'))


def test_the_assistants_own_notes_are_marked_as_weaker_than_the_users(client):
    """They sit next to each other in the prompt and do not carry the same
    authority: the user wrote one of them."""
    from backend import observations

    observations.add_observation('Trains on Tuesdays')
    prompt = build_chat_system_prompt()
    assert 'your own notes' in prompt
    assert 'may be wrong or out of date' in prompt
