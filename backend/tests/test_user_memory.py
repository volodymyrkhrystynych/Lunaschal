"""The standing document the assistant keeps about the user.

The assistant writes here without a confirmation card, so the properties worth
testing are the ones that make that safe: every change is snapshotted, nothing
grows without bound, and a failed revision leaves the document alone.
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
    memory.append_note('Their cat is called Miso (speech-to-text hears "me so")')
    prompt = build_chat_system_prompt()
    assert 'Miso' in prompt
    # The block has to tell the model to trust these spellings over a transcript,
    # or the correction loop this exists for never closes.
    assert 'speech-to-text' in prompt


def test_append_adds_one_bullet_per_note(client):
    memory.append_note('Their gym is Movati')
    memory.append_note('- Their partner is Sam')
    assert memory.get_memory() == '- Their gym is Movati\n- Their partner is Sam'


def test_every_write_snapshots_what_was_there_before(client):
    memory.append_note('first')
    memory.append_note('second')
    revisions = memory.list_revisions()
    # Newest first: the second write saw the document with only 'first' in it,
    # and the first write saw it empty.
    assert [r['content'] for r in revisions] == ['- first', '']
    assert [r['source'] for r in revisions] == ['remember', 'remember']
    assert revisions[0]['note'] == 'second'


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


def test_appending_past_the_cap_is_refused(client):
    memory.set_memory('x' * (memory.MAX_CHARS - 10), source='user')
    with pytest.raises(memory.MemoryFull):
        memory.append_note('a note that no longer fits in the remaining characters')
    # And the document is untouched.
    assert len(memory.get_memory()) == memory.MAX_CHARS - 10


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


def test_remember_writes_immediately_and_stages_nothing(client):
    text, event = tools.run_tool('remember', {'note': 'Their gym is Movati'})

    assert event['ok'] is True
    # No `proposal` key: an immediate write must not also be able to reach the
    # confirm-card path, the same shape `ask_user` takes.
    assert 'proposal' not in event
    assert 'Movati' in memory.get_memory()
    # And the model is told it is already done, so the reply can't offer to save
    # something that is saved — the mirror of what `_staged` tells it.
    assert 'written already' in text
    assert 'nothing has been saved yet' not in text.lower()


def test_remember_refuses_an_empty_note(client):
    text, event = tools.run_tool('remember', {'note': '   '})
    assert event['ok'] is False
    assert memory.get_memory() == ''


def test_remember_hands_a_full_memory_back_as_a_readable_reason(client):
    """`_refused` puts the reason where the model can act on it — here, by
    calling revise_memory to consolidate instead of retrying the append."""
    memory.set_memory('x' * (memory.MAX_CHARS - 5), source='user')
    text, event = tools.run_tool('remember', {'note': 'one more thing'})
    assert event['ok'] is False
    assert 'full' in event['error']


def test_revise_memory_rewrites_in_the_background(client, monkeypatch):
    monkeypatch.setattr('backend.ai.background.run_bg', lambda fn: fn())
    monkeypatch.setattr('backend.ai.memory.revise_memory_document',
                        lambda doc, instruction: '- Their gym is GoodLife')
    memory.set_memory('- Their gym is Movati', source='user')

    text, event = tools.run_tool('revise_memory', {'instruction': 'they switched gyms'})
    assert event['ok'] is True
    assert 'proposal' not in event
    assert memory.get_memory() == '- Their gym is GoodLife'
    assert memory.list_revisions()[0]['source'] == 'revise'


def test_a_failed_revision_leaves_the_document_alone(client, monkeypatch):
    """revise_memory_document returns None when the model was unavailable or
    unusable. Overwriting a page of standing facts with a half-answer is the
    failure that signal exists to prevent."""
    monkeypatch.setattr('backend.ai.background.run_bg', lambda fn: fn())
    monkeypatch.setattr('backend.ai.memory.revise_memory_document',
                        lambda doc, instruction: None)
    memory.set_memory('- Their gym is Movati', source='user')

    tools.run_tool('revise_memory', {'instruction': 'they switched gyms'})
    assert memory.get_memory() == '- Their gym is Movati'


def test_revise_memory_refuses_an_empty_instruction(client):
    text, event = tools.run_tool('revise_memory', {'instruction': ''})
    assert event['ok'] is False


def test_an_over_cap_revision_is_discarded(client, monkeypatch):
    from backend.ai import memory as ai_memory

    monkeypatch.setattr(ai_memory, 'chat_text',
                        lambda prompt, system=None: 'x' * (memory.MAX_CHARS + 1))
    assert ai_memory.revise_memory_document('- current', 'do a thing') is None


def test_a_fenced_revision_is_unwrapped(client, monkeypatch):
    from backend.ai import memory as ai_memory

    monkeypatch.setattr(ai_memory, 'chat_text',
                        lambda prompt, system=None: '```\n- Their gym is GoodLife\n```')
    assert ai_memory.revise_memory_document('- old', 'x') == '- Their gym is GoodLife'
