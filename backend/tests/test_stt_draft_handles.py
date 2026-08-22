"""The draft path's persistent STT handles.

backend/journal/voice_drafts.py runs every backend in DRAFT_BACKENDS over every
clip. Those handles used to be rebuilt per draft, which measured ~5s of the
~13-18s a 30-60s draft takes — a third of it spent reloading models discarded
minutes earlier. They are cached for the life of the process now, and what
matters is that caching them didn't cost any of the properties private handles
were introduced for: they must stay separate from the interactive singleton,
follow a settings change, and not pin a model left in a bad state.
"""
import pytest

from backend.routes import stt


@pytest.fixture(autouse=True)
def _clear_handles():
    stt.reset_draft_handles()
    yield
    stt.reset_draft_handles()


@pytest.fixture
def built(monkeypatch):
    """Records every _build_stt_backend call and hands back a fake handle."""
    calls = []

    def _fake_build(backend, model_name=None, device=None):
        calls.append((backend, model_name, device))
        return {'backend': backend, 'model': object(), 'vad': None,
                'model_name': model_name, 'device': device}

    monkeypatch.setattr(stt, '_build_stt_backend', _fake_build)
    return calls


@pytest.fixture
def transcribed(monkeypatch):
    """Makes _transcribe_with_handle succeed, recording the handles it saw."""
    seen = []

    def _fake(handle, content, filename, language):
        seen.append(handle)
        return {'text': f"text from {handle['backend']}", 'language': 'en'}

    monkeypatch.setattr(stt, '_transcribe_with_handle', _fake)
    return seen


def _run(backends=('parakeet', 'local')):
    return stt.run_multi_backend_transcribe(b'\x00' * 2048, 'a.wav', None, list(backends))


def test_models_are_built_once_and_reused_across_drafts(client, built, transcribed):
    for _ in range(3):
        _run()

    # Three drafts, two backends — six transcriptions off two model loads.
    assert len(transcribed) == 6
    assert len(built) == 2
    assert {c[0] for c in built} == {'parakeet', 'local'}


def test_the_same_handle_object_is_reused(client, built, transcribed):
    _run(['parakeet'])
    _run(['parakeet'])
    assert transcribed[0] is transcribed[1]


def test_both_backends_stay_resident_together(client, built, transcribed):
    """The interactive singleton holds one backend at a time; the draft cache
    must hold every DRAFT_BACKENDS entry at once or it would thrash."""
    _run()
    assert len(stt._draft_handles) == 2


def test_the_cache_is_separate_from_the_interactive_singleton(client, built, transcribed):
    stt._stt_model = sentinel = object()
    stt._loaded_stt_backend = 'parakeet'
    stt._stt_ready = True
    try:
        _run()
        # A draft must not evict what a live dictation has loaded.
        assert stt._stt_model is sentinel
        assert stt._loaded_stt_backend == 'parakeet'
        # …nor reuse it, which would put two threads in one non-thread-safe model.
        assert all(h['model'] is not sentinel for h in transcribed)
    finally:
        stt._stt_model = None
        stt._loaded_stt_backend = None
        stt._stt_ready = False


def test_a_changed_whisper_model_rebuilds_and_evicts_the_old_handle(client, built, transcribed):
    client.patch('/api/settings/ai', json={'whisperModel': 'small'})
    _run(['local'])
    client.patch('/api/settings/ai', json={'whisperModel': 'turbo'})
    _run(['local'])

    assert [c[1] for c in built] == ['small', 'turbo']
    # Only the current config stays resident — the superseded one isn't kept.
    assert len(stt._draft_handles) == 1
    assert list(stt._draft_handles)[0][1] == 'turbo'


def test_a_changed_device_rebuilds_the_handle(client, built, transcribed):
    client.patch('/api/settings/ai', json={'sttDevice': 'cpu'})
    _run(['local'])
    client.patch('/api/settings/ai', json={'sttDevice': 'cuda'})
    _run(['local'])

    assert [c[2] for c in built] == ['cpu', 'cuda']


def test_reload_clears_the_cache_so_the_next_draft_reloads(client, built, transcribed):
    _run()
    assert client.post('/api/stt/reload').status_code == 200
    assert stt._draft_handles == {}

    _run()
    assert len(built) == 4


def test_a_failing_backend_drops_its_handle_rather_than_pinning_a_bad_model(
    client, built, monkeypatch
):
    def _boom(handle, content, filename, language):
        raise RuntimeError('model wedged')

    monkeypatch.setattr(stt, '_transcribe_with_handle', _boom)

    results = _run(['parakeet'])
    assert results == [{'backend': 'parakeet', 'error': 'model wedged'}]
    assert stt._draft_handles == {}

    _run(['parakeet'])
    assert len(built) == 2


def test_short_audio_keeps_the_handle(client, built, monkeypatch):
    """A ValueError is about the clip, not the model — reloading a perfectly
    good model because someone tapped the hotkey would undo the whole point."""
    def _too_short(handle, content, filename, language):
        raise ValueError('Audio too short or empty')

    monkeypatch.setattr(stt, '_transcribe_with_handle', _too_short)

    _run(['parakeet'])
    assert len(stt._draft_handles) == 1
    _run(['parakeet'])
    assert len(built) == 1


def test_one_backend_failing_still_leaves_the_other_cached(client, built, monkeypatch):
    def _selective(handle, content, filename, language):
        if handle['backend'] == 'local':
            raise RuntimeError('whisper died')
        return {'text': 'ok', 'language': 'en'}

    monkeypatch.setattr(stt, '_transcribe_with_handle', _selective)

    results = _run()
    assert results[0] == {'backend': 'parakeet', 'text': 'ok'}
    assert 'error' in results[1]
    assert [k[0] for k in stt._draft_handles] == ['parakeet']
