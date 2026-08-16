"""POST /api/transcribe/correct: fixing what STT misheard against reference
material — the standing memory document (always consulted) plus an optional
pasted-in ground-truth document.
"""
import io

import pytest

from backend.routes import stt


def _post(client, ground_truth=None):
    data = {'audio': (io.BytesIO(b'fake audio bytes'), 'clip.wav')}
    if ground_truth is not None:
        data['ground_truth'] = ground_truth
    return client.post(
        '/api/transcribe/correct', data=data, content_type='multipart/form-data'
    )


@pytest.fixture(autouse=True)
def _stub_transcription(monkeypatch):
    monkeypatch.setattr(stt, '_load_stt', lambda *a, **k: None)
    monkeypatch.setattr(
        stt, '_do_transcribe',
        lambda *a, **k: {'text': 'had vary nikki', 'language': 'en'},
    )


def test_returns_raw_text_unchanged_with_no_reference_at_all(client, monkeypatch):
    monkeypatch.setattr(stt, 'is_ai_configured', lambda: True)
    body = _post(client).get_json()
    assert body == {
        'raw': 'had vary nikki', 'corrected': 'had vary nikki', 'language': 'en',
    }


def test_never_calls_the_model_with_no_reference(client, monkeypatch):
    monkeypatch.setattr(stt, 'is_ai_configured', lambda: True)
    called = []
    monkeypatch.setattr(
        stt, 'chat_stream', lambda *a, **k: (called.append(1), iter(()))[1]
    )
    _post(client)
    assert called == []


def test_corrects_against_the_memory_document_with_no_ground_truth(client, monkeypatch):
    from backend.memory import set_memory

    set_memory('The dish is called vareniki.', source='user')
    monkeypatch.setattr(stt, 'is_ai_configured', lambda: True)

    seen = {}

    def _fake_stream(messages, **kwargs):
        seen['messages'] = messages
        return iter(['had vareniki'])
    monkeypatch.setattr(stt, 'chat_stream', _fake_stream)

    body = _post(client).get_json()
    assert body['corrected'] == 'had vareniki'
    assert 'The dish is called vareniki.' in seen['messages'][0]['content']


def test_still_accepts_a_pasted_ground_truth_alongside_memory(client, monkeypatch):
    from backend.memory import set_memory

    set_memory('The dish is called vareniki.', source='user')
    monkeypatch.setattr(stt, 'is_ai_configured', lambda: True)

    seen = {}

    def _fake_stream(messages, **kwargs):
        seen['messages'] = messages
        return iter(['had vareniki'])
    monkeypatch.setattr(stt, 'chat_stream', _fake_stream)

    body = _post(client, ground_truth='vareniki, a Ukrainian dumpling').get_json()
    assert body['corrected'] == 'had vareniki'
    content = seen['messages'][0]['content']
    assert 'The dish is called vareniki.' in content
    assert 'vareniki, a Ukrainian dumpling' in content


def test_ground_truth_alone_still_works_with_no_memory_set(client, monkeypatch):
    monkeypatch.setattr(stt, 'is_ai_configured', lambda: True)
    seen = {}

    def _fake_stream(messages, **kwargs):
        seen['messages'] = messages
        return iter(['had vareniki'])
    monkeypatch.setattr(stt, 'chat_stream', _fake_stream)

    body = _post(client, ground_truth='vareniki').get_json()
    assert body['corrected'] == 'had vareniki'
    assert 'vareniki' in seen['messages'][0]['content']


def test_returns_raw_text_when_ai_unconfigured_even_with_memory_set(client, monkeypatch):
    from backend.memory import set_memory

    set_memory('The dish is called vareniki.', source='user')
    monkeypatch.setattr(stt, 'is_ai_configured', lambda: False)
    body = _post(client).get_json()
    assert body['corrected'] == 'had vary nikki'
