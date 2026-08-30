"""The standing document the user keeps about themselves.

Chat used to write here itself, with no confirmation card; it no longer can, so
the properties worth testing are the ones that make the user's own edits safe —
every change is snapshotted, nothing grows without bound, and any version can be
put back.
"""
import pytest

from backend import memory
from backend.ai.chat import build_chat_system_prompt
from backend.delegate import tools


def test_starts_empty_and_contributes_nothing_to_the_prompt(client):
    assert memory.get_memory() == ''
    assert memory.format_memory_context() == ''
    assert 'Things you have been asked to remember' not in build_chat_system_prompt()


def test_a_remembered_line_reaches_the_chat_system_prompt(client):
    memory.set_memory('- Their cat is called Miso (speech-to-text hears "me so")',
                      source='user')
    prompt = build_chat_system_prompt()
    assert 'Miso' in prompt
    # The block has to tell the model to trust these spellings over a transcript,
    # or the correction loop this exists for never closes.
    assert 'speech-to-text' in prompt


def test_every_write_snapshots_what_was_there_before(client):
    memory.set_memory('- first', source='user')
    memory.set_memory('- first\n- second', source='user')
    revisions = memory.list_revisions()
    # Newest first: the second write saw the document with only 'first' in it,
    # and the first write saw it empty.
    assert [r['content'] for r in revisions] == ['- first', '']
    assert [r['source'] for r in revisions] == ['user', 'user']


def test_a_write_that_changes_nothing_records_no_revision(client):
    memory.set_memory('- unchanged', source='user')
    memory.set_memory('- unchanged', source='user')
    assert len(memory.list_revisions()) == 1


def test_restore_puts_the_document_back(client):
    memory.set_memory('- the good version', source='user')
    memory.set_memory('- the model mangled it', source='revise')
    [mangling, _] = memory.list_revisions()

    assert memory.restore(mangling['id']) == '- the good version'
    assert memory.get_memory() == '- the good version'


def test_restore_of_an_unknown_revision_is_none(client):
    assert memory.restore('nope') is None


def test_set_memory_rejects_an_over_cap_document(client):
    with pytest.raises(memory.MemoryFull):
        memory.set_memory('x' * (memory.MAX_CHARS + 1), source='user')


# --- Routes ---


def test_get_and_put_round_trip(client):
    r = client.get('/api/memory')
    assert r.get_json() == {'content': '', 'maxChars': memory.MAX_CHARS}

    r = client.put('/api/memory', json={'content': '- Their gym is Movati'})
    assert r.status_code == 200
    assert client.get('/api/memory').get_json()['content'] == '- Their gym is Movati'


def test_put_requires_a_string(client):
    assert client.put('/api/memory', json={'content': 42}).status_code == 400


def test_put_over_the_cap_is_a_400(client):
    r = client.put('/api/memory', json={'content': 'x' * (memory.MAX_CHARS + 1)})
    assert r.status_code == 400


def test_revisions_and_restore_over_http(client):
    client.put('/api/memory', json={'content': '- the good version'})
    client.put('/api/memory', json={'content': '- overwritten'})

    revisions = client.get('/api/memory/revisions').get_json()
    good = next(r for r in revisions if r['content'] == '- the good version')

    r = client.post(f"/api/memory/revisions/{good['id']}/restore")
    assert r.status_code == 200
    assert client.get('/api/memory').get_json()['content'] == '- the good version'


def test_restoring_an_unknown_revision_is_404(client):
    assert client.post('/api/memory/revisions/nope/restore').status_code == 404


# --- The tools ---


def test_chat_cannot_write_the_users_own_document(client):
    """`remember` came back; the thing that made it wrong did not.

    The original pair wrote *this* document with no confirm card, so a passing
    correction became a permanent fact the user had never asked for. The tool
    now appends to backend/observations.py instead — capped, listed in Settings,
    deletable in one click — and this document stays the user's alone. That
    separation is the whole reason the tool was allowed back, so it is what gets
    asserted here rather than the tool's absence.
    """
    from backend import observations

    names = {t['function']['name'] for t in tools.TOOLS}
    assert 'remember' in names
    # The other half of the retired pair edited the document in place. Nothing
    # replaced it, and nothing should: an assistant rewriting the user's own
    # standing facts is the case with no safe version.
    assert 'revise_memory' not in names

    memory.set_memory('- Their gym is Movati', source='user')
    text, event = tools.run_tool('remember', {'fact': 'Trains on Tuesdays'})

    assert event['ok'] is True
    assert memory.get_memory() == '- Their gym is Movati'
    assert [o['content'] for o in observations.pending()] == ['Trains on Tuesdays']

    text, event = tools.run_tool('revise_memory', {'instruction': 'x'})
    assert event['ok'] is False
    assert 'unknown tool' in event['error']


def test_remember_is_told_not_to_announce_itself(client):
    """The predecessor put a "noted" in every reply for a write nobody asked
    for. The tool's own return text is where that gets headed off, because it is
    what the answering turn actually reads."""
    text, _ = tools.run_tool('remember', {'fact': 'Trains on Tuesdays'})
    assert 'Say nothing about it' in text


def test_remember_does_not_write_the_same_fact_twice(client):
    """It can see only the most recent slice of its own notes in the prompt, so
    re-stating a fact it already saved is normal rather than a malfunction."""
    from backend import observations

    tools.run_tool('remember', {'fact': 'Trains on Tuesdays'})
    text, event = tools.run_tool('remember', {'fact': 'trains on tuesdays'})

    assert event['ok'] is True
    assert event.get('duplicate') is True
    assert len(observations.pending()) == 1


def test_the_system_prompt_still_refuses_to_edit_the_users_document(client):
    """A model told it can edit the document will offer to, and the user is then
    told something was saved that never was. The prompt now offers `remember`
    for the assistant's own notes and nothing at all for this document."""
    memory.set_memory('- Their gym is Movati', source='user')
    prompt = build_chat_system_prompt()
    # The document still rides in every prompt — that half never changed.
    assert 'Movati' in prompt
    assert "That document is the user's own and you cannot write" in prompt
    assert '`revise_memory`' not in prompt
