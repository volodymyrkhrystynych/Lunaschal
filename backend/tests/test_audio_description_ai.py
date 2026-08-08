"""Unit tests for backend.ai.audio_description.describe_audio — mirrors the
gating/error-wrapping shape of backend.ai.images.caption_image, since that
module has no test file of its own and journal attachment route tests cover
the caption path by monkeypatching it directly instead."""
import pytest

from backend.ai import audio_description as audio_ai


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content=None, error=None):
        self._content = content
        self._error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, content=None, error=None):
        self.completions = _FakeCompletions(content, error)
        self.chat = _FakeChat(self.completions)


@pytest.fixture(autouse=True)
def slices(monkeypatch):
    """describe_audio's own gating and windowing are what's under test, not
    ffmpeg — a real audio file and binary aren't needed to exercise them.

    Returns the list of `(start, length)` windows actually decoded, which is how
    the windowing tests below check that a slice was asked for rather than the
    whole file. Duration is stubbed short by default so an unrelated test never
    accidentally takes the multi-window path.
    """
    calls = []

    def _fake_wav(path, start=None, length=None):
        calls.append((start, length))
        return 'ZmFrZQ=='

    monkeypatch.setattr(audio_ai, '_wav_b64', _fake_wav)
    monkeypatch.setattr(audio_ai, '_probe_duration', lambda path: 30.0)
    return calls


def test_get_audio_model_reads_the_configured_alias(monkeypatch):
    monkeypatch.setattr(
        audio_ai, 'get_provider_config',
        lambda: {'llama_audio_model': 'gemma4-e4b-audio'},
    )
    assert audio_ai.get_audio_model() == 'gemma4-e4b-audio'
    assert audio_ai.is_audio_configured() is True


def test_get_audio_model_is_none_when_unset(monkeypatch):
    monkeypatch.setattr(audio_ai, 'get_provider_config', lambda: {'llama_audio_model': None})
    assert audio_ai.get_audio_model() is None
    assert audio_ai.is_audio_configured() is False


def test_describe_audio_raises_when_unconfigured(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_ai, 'get_provider_config', lambda: {'llama_audio_model': ''})
    p = tmp_path / 'clip.wav'
    p.write_bytes(b'\x00')
    with pytest.raises(audio_ai.AudioUnavailable, match='No audio-description model configured'):
        audio_ai.describe_audio(p)


def test_describe_audio_raises_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        audio_ai, 'get_provider_config', lambda: {'llama_audio_model': 'gemma4-e4b-audio'}
    )
    with pytest.raises(audio_ai.AudioUnavailable, match='missing'):
        audio_ai.describe_audio(tmp_path / 'nope.wav')


def test_describe_audio_returns_the_model_text_and_sends_the_hint(monkeypatch, tmp_path):
    monkeypatch.setattr(
        audio_ai, 'get_provider_config', lambda: {'llama_audio_model': 'gemma4-e4b-audio'}
    )
    client = _FakeClient(content='  A dog barks twice.  ')
    monkeypatch.setattr(audio_ai, 'get_llama_client', lambda: client)

    p = tmp_path / 'clip.wav'
    p.write_bytes(b'\x00')
    result = audio_ai.describe_audio(p, hint='backyard recording')

    assert result == 'A dog barks twice.'
    call = client.completions.calls[0]
    assert call['model'] == 'gemma4-e4b-audio'
    user_content = call['messages'][1]['content']
    assert 'backyard recording' in user_content[0]['text']
    assert user_content[1]['type'] == 'input_audio'
    assert user_content[1]['input_audio']['format'] == 'wav'


def test_describe_audio_wraps_client_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(
        audio_ai, 'get_provider_config', lambda: {'llama_audio_model': 'gemma4-e4b-audio'}
    )
    client = _FakeClient(error=RuntimeError('connection refused'))
    monkeypatch.setattr(audio_ai, 'get_llama_client', lambda: client)

    p = tmp_path / 'clip.wav'
    p.write_bytes(b'\x00')
    with pytest.raises(audio_ai.AudioUnavailable, match='connection refused'):
        audio_ai.describe_audio(p)


def test_describe_audio_raises_on_empty_result(monkeypatch, tmp_path):
    monkeypatch.setattr(
        audio_ai, 'get_provider_config', lambda: {'llama_audio_model': 'gemma4-e4b-audio'}
    )
    client = _FakeClient(content='   ')
    monkeypatch.setattr(audio_ai, 'get_llama_client', lambda: client)

    p = tmp_path / 'clip.wav'
    p.write_bytes(b'\x00')
    with pytest.raises(audio_ai.AudioUnavailable, match='empty description'):
        audio_ai.describe_audio(p)


# --- Windowing ---------------------------------------------------------------
#
# A recording costs ~6.25 tokens per second of its length, so duration alone is
# what overflows the 16K context. plan_windows is pure precisely so the whole
# policy can be walked here instead of inferred from ffmpeg invocations.

WINDOW = audio_ai._WINDOW_SECONDS
MAX_WINDOWS = audio_ai._MAX_WINDOWS


def test_plan_windows_keeps_a_short_recording_whole():
    assert audio_ai.plan_windows(30.0) == [(0.0, 30.0)]
    assert audio_ai.plan_windows(float(WINDOW)) == [(0.0, float(WINDOW))]


def test_plan_windows_covers_a_long_recording_contiguously():
    duration = WINDOW * 3 + 90.0
    windows = audio_ai.plan_windows(duration)

    assert len(windows) == 4
    assert sum(length for _, length in windows) == pytest.approx(duration)
    # No gaps: each window starts where the last one ended.
    for (start, length), (next_start, _) in zip(windows, windows[1:]):
        assert start + length == pytest.approx(next_start)
    assert windows[-1][1] == pytest.approx(90.0)


def test_plan_windows_samples_a_recording_too_long_to_cover():
    """The two-hour-plus case that produced the 400: past the compute budget the
    windows spread out rather than truncating, so the end is still described."""
    duration = 3.0 * 60 * 60
    windows = audio_ai.plan_windows(duration)

    assert len(windows) == MAX_WINDOWS
    assert windows[0][0] == 0.0
    assert windows[-1][0] + windows[-1][1] == pytest.approx(duration)
    assert all(length == WINDOW for _, length in windows)
    starts = [start for start, _ in windows]
    assert starts == sorted(starts)


def test_a_long_recording_is_sliced_described_and_reduced(monkeypatch, tmp_path, slices):
    monkeypatch.setattr(
        audio_ai, 'get_provider_config', lambda: {'llama_audio_model': 'gemma4-e4b-audio'}
    )
    monkeypatch.setattr(audio_ai, '_probe_duration', lambda path: WINDOW * 2.0)
    client = _FakeClient(content='A dog barks.')
    monkeypatch.setattr(audio_ai, 'get_llama_client', lambda: client)

    reduced = []
    monkeypatch.setattr(
        'backend.ai.llm.chat_text',
        lambda prompt, system=None: reduced.append((prompt, system)) or 'A walk with a dog.',
    )

    p = tmp_path / 'walk.m4a'
    p.write_bytes(b'\x00')
    result = audio_ai.describe_audio(p, hint='morning walk')

    # Two audio calls, each decoding only its own window.
    assert slices == [(0.0, float(WINDOW)), (float(WINDOW), float(WINDOW))]
    assert len(client.completions.calls) == 2
    prompts = [c['messages'][1]['content'][0]['text'] for c in client.completions.calls]
    assert '0:00' in prompts[0] and '10:00' in prompts[0]
    assert '10:00' in prompts[1] and '20:00' in prompts[1]
    assert all('morning walk' in text for text in prompts)

    # The window descriptions are what the chat model works into the answer, and
    # only its output is stored — the notes never reach the user directly.
    prompt, system = reduced[0]
    assert prompt.count('A dog barks.') == 2
    assert '[0:00–10:00]' in prompt
    assert 'morning walk' in prompt
    assert system == audio_ai._REDUCE_SYSTEM
    assert result == 'A walk with a dog.'


def test_reduce_uses_the_chat_model_not_the_audio_one(monkeypatch, tmp_path):
    """The audio alias is loaded for its encoder; the summary is plain text, so
    it goes to the default chat model like every other summarisation here."""
    monkeypatch.setattr(
        audio_ai, 'get_provider_config', lambda: {'llama_audio_model': 'gemma4-e4b-audio'}
    )
    monkeypatch.setattr(audio_ai, '_probe_duration', lambda path: WINDOW * 2.0)
    client = _FakeClient(content='A dog barks.')
    monkeypatch.setattr(audio_ai, 'get_llama_client', lambda: client)
    monkeypatch.setattr('backend.ai.llm.chat_text', lambda prompt, system=None: 'Summary.')

    p = tmp_path / 'walk.m4a'
    p.write_bytes(b'\x00')
    audio_ai.describe_audio(p)

    assert {c['model'] for c in client.completions.calls} == {'gemma4-e4b-audio'}


def test_a_failed_summary_falls_back_to_the_window_notes(monkeypatch, tmp_path):
    """Losing the notes to a failed summary would throw away the whole expensive
    half of the work — a blockier description still describes the recording."""
    monkeypatch.setattr(
        audio_ai, 'get_provider_config', lambda: {'llama_audio_model': 'gemma4-e4b-audio'}
    )
    monkeypatch.setattr(audio_ai, '_probe_duration', lambda path: WINDOW * 2.0)
    client = _FakeClient(content='A dog barks.')
    monkeypatch.setattr(audio_ai, 'get_llama_client', lambda: client)

    def _boom(prompt, system=None):
        raise RuntimeError('llama-server is down')

    monkeypatch.setattr('backend.ai.llm.chat_text', _boom)

    p = tmp_path / 'walk.m4a'
    p.write_bytes(b'\x00')
    result = audio_ai.describe_audio(p)

    assert result == '[0:00–10:00] A dog barks.\n[10:00–20:00] A dog barks.'


def test_one_unreadable_window_does_not_cost_the_others(monkeypatch, tmp_path):
    monkeypatch.setattr(
        audio_ai, 'get_provider_config', lambda: {'llama_audio_model': 'gemma4-e4b-audio'}
    )
    monkeypatch.setattr(audio_ai, '_probe_duration', lambda path: WINDOW * 3.0)
    client = _FakeClient(content='A dog barks.')
    monkeypatch.setattr(audio_ai, 'get_llama_client', lambda: client)

    real_create = client.completions.create

    def _fail_second(**kwargs):
        if len(client.completions.calls) == 1:
            client.completions.calls.append(kwargs)
            raise RuntimeError('decode failed')
        return real_create(**kwargs)

    client.completions.create = _fail_second
    monkeypatch.setattr(
        'backend.ai.llm.chat_text', lambda prompt, system=None: f'Notes: {prompt.count("barks")}'
    )

    p = tmp_path / 'walk.m4a'
    p.write_bytes(b'\x00')
    assert audio_ai.describe_audio(p) == 'Notes: 2'


def test_a_recording_that_fails_every_window_still_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(
        audio_ai, 'get_provider_config', lambda: {'llama_audio_model': 'gemma4-e4b-audio'}
    )
    monkeypatch.setattr(audio_ai, '_probe_duration', lambda path: WINDOW * 2.0)
    client = _FakeClient(error=RuntimeError('exceeds the available context size'))
    monkeypatch.setattr(audio_ai, 'get_llama_client', lambda: client)

    p = tmp_path / 'walk.m4a'
    p.write_bytes(b'\x00')
    with pytest.raises(audio_ai.AudioUnavailable, match='context size'):
        audio_ai.describe_audio(p)


def test_a_sampled_description_says_so(monkeypatch, tmp_path):
    """A description that quietly covers 2 of 3 hours reads as a description of
    the whole recording, so the shortfall is stated rather than left implied."""
    monkeypatch.setattr(
        audio_ai, 'get_provider_config', lambda: {'llama_audio_model': 'gemma4-e4b-audio'}
    )
    monkeypatch.setattr(audio_ai, '_probe_duration', lambda path: 3.0 * 60 * 60)
    client = _FakeClient(content='Traffic noise.')
    monkeypatch.setattr(audio_ai, 'get_llama_client', lambda: client)
    monkeypatch.setattr('backend.ai.llm.chat_text', lambda prompt, system=None: 'A long drive.')

    p = tmp_path / 'drive.mp4'
    p.write_bytes(b'\x00')
    result = audio_ai.describe_audio(p)

    assert result.startswith('A long drive.')
    assert f'Sampled from {MAX_WINDOWS} excerpts' in result
    assert '3:00:00' in result


def test_a_fully_covered_recording_says_nothing_about_sampling(monkeypatch, tmp_path):
    monkeypatch.setattr(
        audio_ai, 'get_provider_config', lambda: {'llama_audio_model': 'gemma4-e4b-audio'}
    )
    monkeypatch.setattr(audio_ai, '_probe_duration', lambda path: WINDOW * 2.0)
    client = _FakeClient(content='A dog barks.')
    monkeypatch.setattr(audio_ai, 'get_llama_client', lambda: client)
    monkeypatch.setattr('backend.ai.llm.chat_text', lambda prompt, system=None: 'A walk.')

    p = tmp_path / 'walk.m4a'
    p.write_bytes(b'\x00')
    assert audio_ai.describe_audio(p) == 'A walk.'


def test_an_unprobeable_file_still_gets_one_whole_file_call(monkeypatch, tmp_path, slices):
    """ffprobe failing costs the windowing, not the description — that's the
    behaviour this module had before windows existed."""
    monkeypatch.setattr(
        audio_ai, 'get_provider_config', lambda: {'llama_audio_model': 'gemma4-e4b-audio'}
    )
    monkeypatch.setattr(audio_ai, '_probe_duration', lambda path: None)
    client = _FakeClient(content='A dog barks.')
    monkeypatch.setattr(audio_ai, 'get_llama_client', lambda: client)

    p = tmp_path / 'clip.wav'
    p.write_bytes(b'\x00')

    assert audio_ai.describe_audio(p) == 'A dog barks.'
    assert slices == [(None, None)]
    assert len(client.completions.calls) == 1


def test_a_window_fits_the_configured_context():
    """The window has to fit what llama/presets.ini actually loads — if the
    preset's ctx-size shrinks, this is what says so."""
    assert WINDOW <= audio_ai._MAX_WINDOW_SECONDS
    audio_tokens = WINDOW * audio_ai._AUDIO_TOKENS_PER_SECOND
    assert audio_tokens + audio_ai._RESERVED_TOKENS <= audio_ai._CONTEXT_TOKENS
