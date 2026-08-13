"""Notes to self: a fixed 1/2/4/7/14-day review ladder (not FSRS — a note has
no correctness signal to grade), immediate-write creation from chat with no
confirm card, and copy-on-write edit history.
"""
import time

from backend import notes
from backend.delegate import tools

DAY = notes.DAY_SECONDS


# --- The ladder ---


def test_ladder_advances_to_the_next_rung():
    assert notes.next_interval_days(1) == 2
    assert notes.next_interval_days(2) == 4
    assert notes.next_interval_days(4) == 7
    assert notes.next_interval_days(7) == 14


def test_ladder_caps_at_fourteen():
    assert notes.next_interval_days(14) == 14
    assert notes.next_interval_days(30) == 14


# --- Creating, listing due ---


def test_a_new_note_is_due_one_day_out(client):
    now = 1_000_000
    note_id = notes.create_note('water the plants', now=now)
    note = notes.get_note(note_id)
    assert note['content'] == 'water the plants'
    assert note['intervalDays'] == 1
    assert notes.list_due(now=now) == []
    assert len(notes.list_due(now=now + DAY)) == 1


def test_creating_an_empty_note_is_refused(client):
    try:
        notes.create_note('   ')
        assert False, 'expected ValueError'
    except ValueError:
        pass


def test_due_list_is_ordered_soonest_first(client):
    now = 1_000_000
    later_id = notes.create_note('later', now=now + 100)
    sooner_id = notes.create_note('sooner', now=now)
    due = notes.list_due(now=now + 10 * DAY)
    assert [n['id'] for n in due] == [sooner_id, later_id]


# --- Dismissing advances the ladder from the moment of dismissal ---


def test_dismiss_advances_to_the_next_rung(client):
    now = 1_000_000
    note_id = notes.create_note('stretch daily', now=now)
    updated = notes.dismiss_note(note_id, now=now + DAY)
    assert updated['intervalDays'] == 2
    assert notes.list_due(now=now + DAY) == []
    assert len(notes.list_due(now=now + 3 * DAY)) == 1


def test_repeated_dismissal_climbs_the_full_ladder_and_caps(client):
    now = 1_000_000
    note_id = notes.create_note('note', now=now)
    for _ in range(6):
        updated = notes.dismiss_note(note_id, now=now)
    assert updated['intervalDays'] == 14


def test_an_overdue_dismiss_reschedules_from_now_not_from_the_missed_due_date(client):
    """A note left overdue for a week shouldn't get to skip rungs — the next
    interval is counted from when it was actually dismissed."""
    now = 1_000_000
    note_id = notes.create_note('note', now=now)
    dismissed_at = now + 30 * DAY
    updated = notes.dismiss_note(note_id, now=dismissed_at)
    assert updated['intervalDays'] == 2
    assert notes.list_due(now=dismissed_at + DAY) == []


def test_dismissing_an_unknown_note_is_none(client):
    assert notes.dismiss_note('nope') is None


# --- Editing tracks a revision, never touches the schedule ---


def test_editing_a_note_keeps_the_old_text_as_a_revision(client):
    now = 1_000_000
    note_id = notes.create_note('origianl typo', now=now)
    notes.edit_note(note_id, 'original, fixed', now=now + 10)

    assert notes.get_note(note_id)['content'] == 'original, fixed'
    revisions = notes.list_revisions(note_id)
    assert len(revisions) == 1
    assert revisions[0]['content'] == 'origianl typo'


def test_editing_to_the_same_content_records_no_revision(client):
    now = 1_000_000
    note_id = notes.create_note('unchanged', now=now)
    notes.edit_note(note_id, 'unchanged', now=now + 10)
    assert notes.list_revisions(note_id) == []


def test_editing_never_touches_the_review_schedule(client):
    now = 1_000_000
    note_id = notes.create_note('note', now=now)
    before = notes.get_note(note_id)
    notes.edit_note(note_id, 'edited note', now=now + 10)
    after = notes.get_note(note_id)
    assert after['due'] == before['due']
    assert after['intervalDays'] == before['intervalDays']


def test_editing_an_unknown_note_is_none(client):
    assert notes.edit_note('nope', 'x') is None


def test_editing_to_empty_is_refused(client):
    now = 1_000_000
    note_id = notes.create_note('note', now=now)
    try:
        notes.edit_note(note_id, '   ')
        assert False, 'expected ValueError'
    except ValueError:
        pass


# --- Routes ---


def test_due_route_returns_due_notes(client, monkeypatch):
    import time as time_mod

    now = 1_000_000
    monkeypatch.setattr(time_mod, 'time', lambda: now)
    notes.create_note('note', now=now - 2 * DAY)

    r = client.get('/api/notes/due')
    assert r.status_code == 200
    body = r.get_json()
    assert len(body) == 1
    assert body[0]['content'] == 'note'


def test_dismiss_route_advances_and_returns_the_note(client):
    note_id = notes.create_note('note')
    r = client.post(f'/api/notes/{note_id}/dismiss')
    assert r.status_code == 200
    assert r.get_json()['intervalDays'] == 2


def test_dismiss_route_404s_for_an_unknown_note(client):
    assert client.post('/api/notes/nope/dismiss').status_code == 404


def test_update_route_edits_content(client):
    note_id = notes.create_note('note')
    r = client.put(f'/api/notes/{note_id}', json={'content': 'edited'})
    assert r.status_code == 200
    assert r.get_json()['content'] == 'edited'


def test_update_route_rejects_blank_content(client):
    note_id = notes.create_note('note')
    r = client.put(f'/api/notes/{note_id}', json={'content': '   '})
    assert r.status_code == 400


def test_update_route_404s_for_an_unknown_note(client):
    r = client.put('/api/notes/nope', json={'content': 'x'})
    assert r.status_code == 404


def test_revisions_route_lists_history(client):
    note_id = notes.create_note('first')
    notes.edit_note(note_id, 'second')
    r = client.get(f'/api/notes/{note_id}/revisions')
    assert r.status_code == 200
    assert [rev['content'] for rev in r.get_json()] == ['first']


def test_revisions_route_404s_for_an_unknown_note(client):
    assert client.get('/api/notes/nope/revisions').status_code == 404


# --- The chat tool ---


def test_create_note_to_self_writes_immediately_and_stages_nothing(client):
    text, event = tools.run_tool('create_note_to_self', {'content': 'buy a birthday card'})

    assert event['ok'] is True
    # No `proposal` key, the same shape `remember` takes: an immediate write
    # must not also be reachable through the confirm-card path.
    assert 'proposal' not in event
    assert 'written already' in text

    due = notes.list_due(now=int(time.time()) + 2 * DAY)
    assert any(n['content'] == 'buy a birthday card' for n in due)


def test_create_note_to_self_refuses_empty_content(client):
    text, event = tools.run_tool('create_note_to_self', {'content': '  '})
    assert event['ok'] is False
    assert 'nothing to note yet' in text


def test_create_note_to_self_truncates_at_the_char_cap(client):
    tools.run_tool('create_note_to_self', {'content': 'x' * (notes.MAX_CONTENT_CHARS + 500)})
    due = notes.list_due(now=int(time.time()) + 2 * DAY)
    assert len(due[0]['content']) == notes.MAX_CONTENT_CHARS
