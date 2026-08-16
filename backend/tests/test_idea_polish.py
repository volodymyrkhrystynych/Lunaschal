"""Voice-captured ideas get `content` filled in by a background polish pass,
mirroring Journal's raw_content/content contract (backend/research/CLAUDE.md).
"""
import pytest

from backend.ai import idea_polish
from backend.routes import ideas as ideas_routes


@pytest.fixture(autouse=True)
def _sync_bg(monkeypatch):
    """Run the background polish job inline instead of on a thread, so its
    DB write can be asserted on without a race."""
    monkeypatch.setattr(ideas_routes, 'run_bg', lambda fn: fn())


# --- backend/ai/idea_polish.py -----------------------------------------------

def test_polish_returns_empty_when_ai_unconfigured(monkeypatch):
    monkeypatch.setattr(idea_polish, 'is_ai_configured', lambda: False)
    assert idea_polish.polish_idea('a grid of habits') == ''


def test_polish_returns_empty_on_failure(monkeypatch):
    monkeypatch.setattr(idea_polish, 'is_ai_configured', lambda: True)

    def _boom(*a, **k):
        raise RuntimeError('Connection error.')
    monkeypatch.setattr(idea_polish, 'chat_text', _boom)

    assert idea_polish.polish_idea('a grid of habits') == ''


def test_polish_strips_preamble_and_quotes(monkeypatch):
    monkeypatch.setattr(idea_polish, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(
        idea_polish, 'chat_text',
        lambda *a, **k: 'Here is the cleaned text:\n"A grid of habits."',
    )
    assert idea_polish.polish_idea('a grid of habits') == 'A grid of habits.'


def test_polish_passes_memory_as_context(monkeypatch):
    monkeypatch.setattr(idea_polish, 'is_ai_configured', lambda: True)
    seen = {}

    def _fake(prompt, system=None):
        seen['prompt'] = prompt
        return 'corrected'
    monkeypatch.setattr(idea_polish, 'chat_text', _fake)

    idea_polish.polish_idea('had vary nikki', memory='The dog is named Fenwick.')
    assert 'The dog is named Fenwick.' in seen['prompt']


def test_polish_runs_with_no_memory_at_all(monkeypatch):
    # Still does the light cleanup — memory is optional, not required to run.
    monkeypatch.setattr(idea_polish, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(idea_polish, 'chat_text', lambda *a, **k: 'A grid of habits.')
    assert idea_polish.polish_idea('a grid of habits') == 'A grid of habits.'


# --- POST /api/ideas/voice ----------------------------------------------------

def test_voice_capture_fills_in_content_from_polish(client, monkeypatch):
    monkeypatch.setattr('backend.ai.idea_polish.is_ai_configured', lambda: True)
    monkeypatch.setattr(
        'backend.ai.idea_polish.chat_text',
        lambda *a, **k: 'A grid of habits in the day view.',
    )

    r = client.post(
        '/api/ideas/voice', json={'rawContent': 'grid of habits in the day view'}
    )
    idea_id = r.get_json()['id']

    body = client.get(f'/api/ideas/{idea_id}').get_json()
    assert body['rawContent'] == 'grid of habits in the day view'
    assert body['content'] == 'A grid of habits in the day view.'


def test_voice_capture_leaves_content_empty_when_ai_unavailable(client):
    r = client.post('/api/ideas/voice', json={'rawContent': 'grid of habits'})
    idea_id = r.get_json()['id']
    body = client.get(f'/api/ideas/{idea_id}').get_json()
    assert body['content'] == ''


def test_voice_capture_passes_the_memory_document_to_the_polish(client, monkeypatch):
    from backend.memory import set_memory

    set_memory('The dog is named Fenwick.', source='user')
    monkeypatch.setattr('backend.ai.idea_polish.is_ai_configured', lambda: True)

    seen = {}

    def _fake(prompt, system=None):
        seen['prompt'] = prompt
        return 'corrected'
    monkeypatch.setattr('backend.ai.idea_polish.chat_text', _fake)

    client.post('/api/ideas/voice', json={'rawContent': 'had vary nikki'})
    assert 'The dog is named Fenwick.' in seen['prompt']
