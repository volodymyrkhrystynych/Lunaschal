"""Correcting a dictated chat message before it is sent.

The two references are the whole feature: the standing memory (names the user
already corrected once) and what a vision model read out of the attached photo
(a menu or label spelling the word that was misheard). With neither, the pass
must not run at all — a model asked to improve a transcript with nothing to
check it against rewrites it, and rewriting is the one thing that must never
happen to a record of what someone said.
"""
import io

import pytest
from PIL import Image

from backend import memory
from backend.ai import transcript as ai_transcript
from backend.routes import chat as chat_routes


@pytest.fixture(autouse=True)
def chat_root(monkeypatch, tmp_path):
    monkeypatch.setenv('CHAT_ROOT', str(tmp_path / 'chat'))


@pytest.fixture(autouse=True)
def sync_bg(monkeypatch):
    monkeypatch.setattr(chat_routes, 'run_bg', lambda fn: fn())


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(ai_transcript, 'is_ai_configured', lambda: True)


def _jpeg():
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), (120, 120, 120)).save(buf, 'JPEG')
    return buf.getvalue()


def _capture(monkeypatch, corrected='had vareniki at Movati'):
    """Record the prompt the corrector was given, and answer with `corrected`."""
    seen = {}

    def _fake(prompt, system=None, schema=None, thinking=None, max_tokens=None):
        seen['prompt'] = prompt
        seen['system'] = system
        return {'corrected': corrected}

    monkeypatch.setattr(ai_transcript, 'chat_json', _fake)
    return seen


def _attach(client):
    conv = client.post('/api/chat/conversations', json={}).get_json()['id']
    r = client.post(f'/api/chat/conversations/{conv}/attachments',
                    data={'image': (io.BytesIO(_jpeg()), 'meal.jpg')},
                    content_type='multipart/form-data')
    return conv, r.get_json()[0]['id']


# --- The corrector itself ---


def test_the_memory_is_used_as_a_glossary(monkeypatch):
    seen = _capture(monkeypatch)
    out = ai_transcript.correct_transcript(
        'had vary nikki at motivate',
        memory='- Their gym is Movati (speech-to-text hears "motivate")',
    )
    assert out == 'had vareniki at Movati'
    assert 'Movati' in seen['prompt']


def test_a_photo_reading_is_used_as_a_glossary(monkeypatch):
    seen = _capture(monkeypatch)
    ai_transcript.correct_transcript(
        'had vary nikki', photo_notes=['The menu board reads "VARENIKI".']
    )
    assert 'VARENIKI' in seen['prompt']


def test_with_no_reference_the_model_is_never_called(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError('the corrector ran with nothing to correct against')

    monkeypatch.setattr(ai_transcript, 'chat_json', _boom)
    assert ai_transcript.correct_transcript('had vary nikki') == 'had vary nikki'


def test_a_failed_call_returns_the_transcript_unchanged(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError('llama-server is down')

    monkeypatch.setattr(ai_transcript, 'chat_json', _boom)
    assert ai_transcript.correct_transcript(
        'had vary nikki', memory='- something'
    ) == 'had vary nikki'


def test_an_empty_correction_is_discarded(monkeypatch):
    monkeypatch.setattr(ai_transcript, 'chat_json', lambda *a, **kw: {'corrected': '   '})
    assert ai_transcript.correct_transcript(
        'had vary nikki', memory='- something'
    ) == 'had vary nikki'


def test_an_unconfigured_model_returns_the_transcript(monkeypatch):
    monkeypatch.setattr(ai_transcript, 'is_ai_configured', lambda: False)
    monkeypatch.setattr(ai_transcript, 'chat_json',
                        lambda *a, **kw: {'corrected': 'should not be used'})
    assert ai_transcript.correct_transcript('raw', memory='- x') == 'raw'


def test_the_prompt_forbids_rewriting(monkeypatch):
    seen = _capture(monkeypatch)
    ai_transcript.correct_transcript('anything', memory='- x')
    assert 'Never reword' in seen['system']


# --- The route ---


def test_the_route_returns_both_texts(client, monkeypatch):
    _capture(monkeypatch)
    memory.set_memory('- Their gym is Movati', source='user')
    r = client.post('/api/chat/polish-transcript',
                    json={'text': 'had vary nikki at motivate'})
    assert r.status_code == 200
    assert r.get_json() == {'raw': 'had vary nikki at motivate',
                            'corrected': 'had vareniki at Movati'}


def test_the_route_feeds_in_the_attached_photos_reading(client, monkeypatch):
    monkeypatch.setattr(chat_routes, '_do_read_attachment',
                        lambda path: 'The menu board reads "VARENIKI".')
    _, attachment_id = _attach(client)
    seen = _capture(monkeypatch)

    client.post('/api/chat/polish-transcript',
                json={'text': 'had vary nikki', 'attachmentIds': [attachment_id]})
    assert 'VARENIKI' in seen['prompt']


def test_a_photo_that_could_not_be_read_contributes_nothing(client, monkeypatch):
    def _boom(path):
        raise RuntimeError('no vision model')

    monkeypatch.setattr(chat_routes, '_do_read_attachment', _boom)
    _, attachment_id = _attach(client)

    def _never(*a, **kw):
        raise AssertionError('the corrector ran with nothing to correct against')

    monkeypatch.setattr(ai_transcript, 'chat_json', _never)
    r = client.post('/api/chat/polish-transcript',
                    json={'text': 'had vary nikki', 'attachmentIds': [attachment_id]})
    assert r.get_json()['corrected'] == 'had vary nikki'


def test_the_route_needs_text(client):
    assert client.post('/api/chat/polish-transcript', json={'text': '  '}).status_code == 400
