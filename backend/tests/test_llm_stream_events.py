"""Splitting a streamed completion into its content and thinking channels.

Reasoning reaches us two different ways depending on how llama-server was
launched — a separate `reasoning_content` field, or inline in `content` wrapped
in a <think> block — and which one you get is a property of the server's flags,
not of the request. The inline case is the hard one: the tags arrive split
across chunks, so there is no complete string to regex against until the stream
is over, by which point the answer should already be on screen.
"""
from types import SimpleNamespace

import pytest

from backend.ai import llm


def _chunk(content=None, reasoning=None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


@pytest.fixture
def stream(monkeypatch):
    """Feed chat_stream_events a fixed sequence of chunks."""
    def feed(chunks):
        class FakeCompletions:
            def create(self, **kwargs):
                return iter(chunks)

        monkeypatch.setattr(llm, 'get_provider_config', lambda: {})
        monkeypatch.setattr(llm, 'get_model', lambda c: 'qwen36')
        monkeypatch.setattr(llm, 'default_generation_opts',
                            lambda: {'thinking': True, 'max_tokens': 100})
        monkeypatch.setattr(llm, 'get_llama_client', lambda c: SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())))
        return list(llm.chat_stream_events([{'role': 'user', 'content': 'hi'}]))
    return feed


def _joined(events, kind):
    return ''.join(text for k, text in events if k == kind)


def test_a_reply_with_no_thinking_is_all_content(stream):
    events = stream([_chunk('Hello'), _chunk(' there')])
    assert _joined(events, 'content') == 'Hello there'
    assert _joined(events, 'thinking') == ''


def test_the_reasoning_content_field_is_labelled_thinking(stream):
    events = stream([_chunk(reasoning='weighing it'), _chunk('Hello')])
    assert _joined(events, 'thinking') == 'weighing it'
    assert _joined(events, 'content') == 'Hello'


def test_an_inline_think_block_is_split_out(stream):
    events = stream([_chunk('<think>pondering</think>Hello')])
    assert _joined(events, 'thinking') == 'pondering'
    assert _joined(events, 'content') == 'Hello'


def test_think_tags_split_across_chunks_are_still_recognised(stream):
    """The failure this guards: '<' emitted as answer text because the rest of
    the tag hadn't arrived yet, leaving '<think>' rendered in the reply."""
    events = stream([
        _chunk('<'), _chunk('think'), _chunk('>'),
        _chunk('pond'), _chunk('ering'),
        _chunk('</'), _chunk('think'), _chunk('>'),
        _chunk('Hello'),
    ])
    assert _joined(events, 'content') == 'Hello'
    assert _joined(events, 'thinking') == 'pondering'


def test_a_lone_angle_bracket_is_not_swallowed(stream):
    """Holding back a possible partial tag must not lose text that turns out
    to be ordinary prose — '5 < 6' has to survive."""
    events = stream([_chunk('5 <'), _chunk(' 6 is true')])
    assert _joined(events, 'content') == '5 < 6 is true'


def test_a_trailing_partial_tag_is_flushed_at_the_end(stream):
    """A stream that ends mid-tag must still emit what it was holding."""
    events = stream([_chunk('Hello <thi')])
    assert _joined(events, 'content') == 'Hello <thi'


def test_an_unclosed_think_block_stays_thinking(stream):
    """Better to withhold a fragment as reasoning than to render a half-finished
    thought as the answer."""
    events = stream([_chunk('<think>still going')])
    assert _joined(events, 'content') == ''
    assert _joined(events, 'thinking') == 'still going'


def test_chat_stream_deltas_yields_only_the_answer(stream, monkeypatch):
    """Every existing caller wants the answer alone and must not have to filter
    the thinking back out."""
    def feed_deltas(chunks):
        class FakeCompletions:
            def create(self, **kwargs):
                return iter(chunks)

        monkeypatch.setattr(llm, 'get_provider_config', lambda: {})
        monkeypatch.setattr(llm, 'get_model', lambda c: 'qwen36')
        monkeypatch.setattr(llm, 'default_generation_opts',
                            lambda: {'thinking': True, 'max_tokens': 100})
        monkeypatch.setattr(llm, 'get_llama_client', lambda c: SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())))
        return ''.join(llm.chat_stream_deltas([{'role': 'user', 'content': 'hi'}]))

    assert feed_deltas([
        _chunk('<think>pondering</think>'), _chunk(reasoning='more'), _chunk('Hello'),
    ]) == 'Hello'


def test_chunks_without_choices_are_skipped(stream):
    """llama-server emits a final usage-only chunk with an empty choices list."""
    events = stream([_chunk('Hi'), SimpleNamespace(choices=[])])
    assert _joined(events, 'content') == 'Hi'
