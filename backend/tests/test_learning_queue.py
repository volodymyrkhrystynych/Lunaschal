"""Approval-queue lifecycle: generate → pending → approve/regenerate/deny."""
import json
from types import SimpleNamespace

import pytest

from backend.ai import learning_generation


@pytest.fixture
def fake_generate(monkeypatch):
    """Stub the generation module with canned cards; records call args."""
    calls = {}

    def _generate(text, direction=None, memory='', **kwargs):
        calls['generate'] = {'text': text, 'direction': direction, 'memory': memory}
        return [
            {'question': 'What is X?', 'answer': 'X is a thing.'},
            {'question': 'What is Y?', 'answer': 'Y is another thing.'},
        ]

    def _regenerate(question, answer, generation_context, direction):
        calls['regenerate'] = {
            'question': question, 'context': generation_context, 'direction': direction,
        }
        return [
            {'question': 'Split A?', 'answer': 'Answer A.'},
            {'question': 'Split B?', 'answer': 'Answer B.'},
        ]

    monkeypatch.setattr(learning_generation, 'generate_cards', _generate)
    monkeypatch.setattr(learning_generation, 'regenerate_cards', _regenerate)
    return calls


def test_generate_lands_in_queue(client, fake_generate):
    r = client.post('/api/learning/generate', json={'text': 'brain dump about X and Y'})
    assert r.status_code == 200
    assert r.json['count'] == 2

    queue = client.get('/api/learning/queue').json
    assert len(queue) == 2
    assert {c['state'] for c in queue} == {'pending'}
    assert queue[0]['sourceType'] == 'braindump'
    # Pending cards are not active/browsable and have no due date yet.
    assert client.get('/api/learning/cards').json == []
    assert queue[0]['due'] is None


def test_generate_requires_text(client, fake_generate):
    assert client.post('/api/learning/generate', json={}).status_code == 400


def test_generate_passes_the_memory_document_to_generation(client, fake_generate):
    from backend.memory import set_memory

    set_memory('The framework is called Lunaschal.', source='user')
    client.post('/api/learning/generate', json={'text': 'brain dump about X and Y'})
    assert fake_generate['generate']['memory'] == 'The framework is called Lunaschal.'


def test_approve_activates_card(client, fake_generate):
    ids = client.post('/api/learning/generate', json={'text': 'dump'}).json['ids']
    r = client.post(f'/api/learning/queue/{ids[0]}/approve', json={})
    assert r.status_code == 200
    assert r.json['status'] == 'approved'
    assert r.json['due'] is not None

    card = client.get(f'/api/learning/cards/{ids[0]}').json
    assert card['state'] == 'active'
    assert card['due'] is not None
    assert len(client.get('/api/learning/queue').json) == 1

    # Approving twice (or approving a non-pending card) is a 404.
    assert client.post(f'/api/learning/queue/{ids[0]}/approve', json={}).status_code == 404


def test_regenerate_replaces_card_and_preserves_lineage(client, fake_generate):
    parent = client.post('/api/learning/cards', json={'question': 'P?', 'answer': 'P.'}).json['id']
    ids = client.post('/api/learning/generate', json={
        'text': 'dump', 'derivedFrom': parent, 'tags': ['topic'],
    }).json['ids']

    r = client.post(f'/api/learning/queue/{ids[0]}/regenerate', json={'direction': 'too broad, split it'})
    assert r.status_code == 200
    new_ids = r.json['ids']
    assert len(new_ids) == 2
    assert fake_generate['regenerate']['direction'] == 'too broad, split it'
    assert fake_generate['regenerate']['context'] == 'dump'
    # The drafted question/answer text comes back too, so a caller (chat's
    # inline note-to-self preview) can update its view without a refetch.
    assert [c['question'] for c in r.json['cards']] == ['Split A?', 'Split B?']
    assert r.json['cards'][0]['id'] == new_ids[0]

    # Original gone, replacements pending with lineage/tags carried over.
    assert client.get(f'/api/learning/cards/{ids[0]}').status_code == 404
    for id in new_ids:
        card = client.get(f'/api/learning/cards/{id}').json
        assert card['state'] == 'pending'
        assert card['derivedFrom'] == parent
        assert card['tags'] == ['topic']

    assert client.post(f'/api/learning/queue/{new_ids[0]}/regenerate', json={}).status_code == 400


def test_deny_deletes_pending_only(client, fake_generate):
    ids = client.post('/api/learning/generate', json={'text': 'dump'}).json['ids']
    assert client.delete(f'/api/learning/queue/{ids[0]}').status_code == 200
    assert client.get(f'/api/learning/cards/{ids[0]}').status_code == 404

    client.post(f'/api/learning/queue/{ids[1]}/approve', json={})
    assert client.delete(f'/api/learning/queue/{ids[1]}').status_code == 404


def test_generate_from_journal(client, fake_generate):
    assert client.post('/api/learning/generate-from-journal', json={'journalId': 'nope'}).status_code == 404

    from backend.db.connection import get_db
    get_db().execute(
        "INSERT INTO journal_entries(id, content, title, created_at, updated_at)"
        " VALUES ('j1', 'entry content', 'Entry', 1, 1)"
    )
    get_db().commit()

    r = client.post('/api/learning/generate-from-journal', json={'journalId': 'j1'})
    assert r.status_code == 200
    card = client.get(f"/api/learning/cards/{r.json['ids'][0]}").json
    assert card['sourceType'] == 'journal'
    assert card['sourceId'] == 'j1'
    assert 'entry content' in fake_generate['generate']['text']


def test_generate_for_topic(client, fake_generate):
    r = client.post('/api/learning/generate-for-topic', json={'topic': 'closures'})
    assert r.status_code == 200
    card = client.get(f"/api/learning/cards/{r.json['ids'][0]}").json
    assert card['sourceType'] == 'chat'
    assert 'closures' in fake_generate['generate']['text']


def test_generate_from_note_requires_content(client, fake_generate):
    assert client.post('/api/learning/generate-from-note', json={}).status_code == 400


def test_generate_from_note_creates_lessons_folder(client, fake_generate):
    r = client.post('/api/learning/generate-from-note', json={'content': 'always warm up first'})
    assert r.status_code == 200
    assert 'always warm up first' in fake_generate['generate']['text']

    card = client.get(f"/api/learning/cards/{r.json['ids'][0]}").json
    assert card['sourceType'] == 'note_to_self'
    assert card['tags'] == ['lessons']

    folders = client.get('/api/learning/folders').json
    lessons = [f for f in folders if f['name'] == 'lessons']
    assert len(lessons) == 1
    assert card['folderId'] == lessons[0]['id'] == r.json['folderId']

    # A second note reuses the same folder instead of creating another.
    r2 = client.post('/api/learning/generate-from-note', json={'content': 'stretch after too'})
    assert r2.json['folderId'] == lessons[0]['id']
    assert len(client.get('/api/learning/folders').json) == len(folders)


def test_generation_prompt_parses_and_enforces_shape(monkeypatch):
    """generate_cards drops malformed entries and calls chat_json."""
    content = json.dumps({'cards': [
        {'question': 'Q1?', 'answer': 'A1'},
        {'question': '', 'answer': 'dropped'},
        {'nonsense': True},
    ]})
    captured = {}

    def fake_chat_json(prompt, system=None, **kwargs):
        captured['prompt'] = prompt
        captured['system'] = system
        captured['kwargs'] = kwargs
        return json.loads(content)

    monkeypatch.setattr(learning_generation, 'chat_json', fake_chat_json)

    cards = learning_generation.generate_cards('some text', direction='keep it atomic')
    assert cards == [{'question': 'Q1?', 'answer': 'A1'}]
    assert 'ONE atomic concept' in captured['system']
    assert 'keep it atomic' in captured['prompt']


def test_generation_prompt_carries_the_memory_document_as_context(monkeypatch):
    captured = {}

    def fake_chat_json(prompt, system=None, **kwargs):
        captured['prompt'] = prompt
        return {'cards': []}

    monkeypatch.setattr(learning_generation, 'chat_json', fake_chat_json)

    learning_generation.generate_cards(
        'some text', memory='The framework is called Lunaschal.'
    )
    assert 'The framework is called Lunaschal.' in captured['prompt']


def test_generation_prompt_carries_no_context_block_without_memory(monkeypatch):
    captured = {}

    def fake_chat_json(prompt, system=None, **kwargs):
        captured['prompt'] = prompt
        return {'cards': []}

    monkeypatch.setattr(learning_generation, 'chat_json', fake_chat_json)

    learning_generation.generate_cards('some text')
    assert 'Context:' not in captured['prompt']
