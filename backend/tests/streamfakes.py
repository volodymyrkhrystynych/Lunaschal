"""A fake llama-server stream, shared by the tests that stub the client.

Every chat completion in the app is now issued with `stream=True` — that is
what makes a background call abandonable partway, and so what makes preemption
real (see backend/ai/service.py). A test double therefore has to model a
*stream* of deltas rather than a finished response object, which is what these
helpers are for.
"""
from types import SimpleNamespace


def chunk(content=None, reasoning=None, finish_reason=None, tool_calls=None):
    """One delta off the wire."""
    delta = SimpleNamespace(content=content, reasoning_content=reasoning,
                            tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)]
    )


def tool_call_delta(index, *, id=None, name=None, arguments=None):
    """A fragment of a tool call. Arguments arrive split across chunks, which is
    the whole reason the accumulator keys them by `index`."""
    return SimpleNamespace(
        index=index, id=id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class FakeStream:
    """Iterable of chunks that records whether it was closed.

    Closing matters: it is what llama-server sees as a client disconnect, and
    so what actually frees the slot when a call is preempted.
    """

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.closed = False

    def __iter__(self):
        return iter(self._chunks)

    def close(self):
        self.closed = True


class FakeCompletions:
    """Stands in for `client.chat.completions`, capturing each call's kwargs."""

    def __init__(self, chunks=None, error=None):
        self._chunks = chunks if chunks is not None else [chunk('{"ok": true}')]
        self._error = error
        self.calls = []
        self.streams = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        stream = FakeStream(self._chunks)
        self.streams.append(stream)
        return stream


class FakeClient:
    def __init__(self, chunks=None, error=None):
        self.completions = FakeCompletions(chunks, error)
        self.chat = SimpleNamespace(completions=self.completions)

    @property
    def calls(self):
        return self.completions.calls


def text_stream(text, *, finish_reason=None):
    """The common case: one message's worth of content."""
    return [chunk(text, finish_reason=finish_reason)]
