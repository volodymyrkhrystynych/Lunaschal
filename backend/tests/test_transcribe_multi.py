"""Every dictation surface transcribing with two models and reconciling them.

POST /api/transcribe is the one endpoint behind the three listener hotkeys and
the twelve in-app microphone buttons, so what it does is what "speech-to-text"
means everywhere in the app. It now runs every backend in MULTI_BACKENDS and
has the LLM cross-check them — gated by the same setting that used to switch a
single-transcript polish pass, because running both models without reconciling
them costs twice the CPU for no better a transcript.
"""
import io

import pytest

from backend.routes import stt


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch):
    """Neither the model loader nor llama-server is reachable in tests."""
    monkeypatch.setattr(stt, '_build_stt_backend',
                        lambda backend, model_name=None, device=None: {
                            'backend': backend, 'model': object(), 'vad': None,
                            'model_name': model_name, 'device': device})
    stt.reset_draft_handles()
    yield
    stt.reset_draft_handles()


@pytest.fixture
def heard(monkeypatch):
    """Give each backend its own slightly-different transcript."""
    texts = {'parakeet': 'i met simon at the cafe', 'local': 'I met Simone at the café'}

    def _fake(handle, content, filename, language):
        return {'text': texts[handle['backend']], 'language': 'en'}

    monkeypatch.setattr(stt, '_transcribe_with_handle', _fake)
    return texts


@pytest.fixture
def merged(monkeypatch):
    """Capture what the merge was asked to reconcile."""
    seen = {}

    def _fake_merge(candidates):
        seen['candidates'] = list(candidates)
        return 'I met Simone at the café.'

    monkeypatch.setattr('backend.ai.transcribe_polish.merge_transcripts', _fake_merge)
    return seen


def _post(client, path='/api/transcribe', **form):
    return client.post(
        path,
        data={'audio': (io.BytesIO(b'\x00' * 2048), 'r.wav', 'audio/wav'), **form},
        content_type='multipart/form-data',
    )


def _enable(client, on=True):
    client.patch('/api/settings/ai', json={'transcribePolishEnabled': on})


# --- the multi-backend path ----------------------------------------------------

def test_transcribe_runs_every_backend_and_returns_the_merged_text(
    client, heard, merged
):
    _enable(client)
    r = _post(client)
    assert r.status_code == 200
    assert r.json['text'] == 'I met Simone at the café.'
    # Both models' readings reached the merge — that is the whole point.
    assert set(merged['candidates']) == set(heard.values())


def test_the_configured_backend_leads_the_candidate_list(client, heard, merged):
    """merge_transcripts falls back to candidates[0] when the LLM is down, so
    the one we'd have picked anyway has to be first."""
    _enable(client)
    client.patch('/api/settings/ai', json={'sttBackend': 'local'})
    _post(client)
    assert merged['candidates'][0] == heard['local']


def test_disabled_takes_the_single_configured_backend_only(client, heard, monkeypatch):
    _enable(client, False)
    built = []
    monkeypatch.setattr(stt, '_build_stt_backend',
                        lambda b, model_name=None, device=None: (
                            built.append(b),
                            {'backend': b, 'model': object(), 'vad': None,
                             'model_name': model_name, 'device': device})[1])

    r = _post(client)
    assert r.status_code == 200
    # Off means one model, not two-without-reconciling.
    assert built == ['parakeet']
    assert r.json['text'] == heard['parakeet']


def test_one_backend_failing_still_returns_a_transcript(client, monkeypatch, merged):
    _enable(client)

    def _half(handle, content, filename, language):
        if handle['backend'] == 'local':
            raise RuntimeError('whisper died')
        return {'text': 'parakeet heard this', 'language': 'en'}

    monkeypatch.setattr(stt, '_transcribe_with_handle', _half)

    r = _post(client)
    assert r.status_code == 200
    assert merged['candidates'] == ['parakeet heard this']


def test_every_backend_failing_is_a_500(client, monkeypatch):
    _enable(client)
    monkeypatch.setattr(stt, '_transcribe_with_handle',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('nope')))

    r = _post(client)
    assert r.status_code == 500
    assert 'parakeet' in r.json['error'] and 'local' in r.json['error']


def test_a_dead_llama_server_degrades_to_the_primary_transcript(
    client, heard, monkeypatch
):
    """/api/transcribe must always return text — every dictation surface in the
    app depends on it, so an offline LLM cannot turn dictation into an error."""
    monkeypatch.setattr('backend.ai.provider.is_ai_configured', lambda: True)
    monkeypatch.setattr('backend.ai.transcribe_polish.chat_text',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('connection refused')))
    _enable(client)

    r = _post(client)
    assert r.status_code == 200
    assert r.json['text'] == heard['parakeet']


def test_transcriptions_are_still_logged(client, heard, merged):
    _enable(client)
    _post(client, source='paste', app='vivaldi')
    rows = client.get('/api/transcriptions').get_json()
    assert rows[0]['text'] == 'I met Simone at the café.'


# --- merge_transcripts itself --------------------------------------------------

def test_merge_never_reformats_into_paragraphs(client, monkeypatch):
    """Journal's merge_voice_draft deliberately breaks paragraphs; this one must
    not, because the caller may be dictating a phrase into a text field."""
    from backend.ai import transcribe_polish

    sent = {}
    monkeypatch.setattr(transcribe_polish, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(transcribe_polish, 'chat_text',
                        lambda prompt, system: sent.update(system=system) or 'out')

    transcribe_polish.merge_transcripts(['a b c', 'a b d'])
    assert 'Break it into multiple paragraphs' in sent['system']
    assert 'Never do this' in sent['system']


def test_a_single_candidate_is_polished_not_merged(client, monkeypatch):
    """Cross-checking one transcript against itself just spends tokens."""
    from backend.ai import transcribe_polish

    calls = []
    monkeypatch.setattr(transcribe_polish, 'polish_transcript',
                        lambda t: calls.append(t) or 'polished')

    assert transcribe_polish.merge_transcripts(['only one']) == 'polished'
    assert calls == ['only one']


def test_merge_drops_blank_candidates(client, monkeypatch):
    from backend.ai import transcribe_polish

    monkeypatch.setattr(transcribe_polish, 'polish_transcript', lambda t: t)
    assert transcribe_polish.merge_transcripts(['', '   ', 'real']) == 'real'
    assert transcribe_polish.merge_transcripts(['', '  ']) == ''


def test_merge_without_ai_returns_the_first_candidate(client, monkeypatch):
    from backend.ai import transcribe_polish

    monkeypatch.setattr(transcribe_polish, 'is_ai_configured', lambda: False)
    assert transcribe_polish.merge_transcripts(['first', 'second']) == 'first'


# --- the shared primary-candidate rule -----------------------------------------

def test_pick_primary_prefers_the_configured_backend():
    candidates = [{'backend': 'parakeet', 'text': 'p'}, {'backend': 'local', 'text': 'l'}]
    assert stt.pick_primary(candidates, 'local')['text'] == 'l'


def test_pick_primary_falls_back_to_parakeet_then_to_whatever_succeeded():
    candidates = [{'backend': 'local', 'text': 'l'}, {'backend': 'parakeet', 'text': 'p'}]
    assert stt.pick_primary(candidates, 'openai')['text'] == 'p'
    assert stt.pick_primary([{'backend': 'local', 'text': 'l'}], 'openai')['text'] == 'l'
