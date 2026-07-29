"""The model sees when each turn was sent, plus the current wall-clock time.

Without both it can't tell whether the user's "in 20 minutes" was said just now
or three hours ago.
"""
import re
from datetime import datetime

import pytest

from backend.ai.chat import (
    SYSTEM_PROMPT,
    TIME_PREFIX_NOTE,
    chat_stream,
    format_now_context,
    stamp_messages,
)

# 2026-07-27 21:58 local — built from local components so the expected
# rendering holds in whatever timezone the suite runs in.
NOW = int(datetime(2026, 7, 27, 21, 58).timestamp())


def _iso(*args) -> str:
    return datetime(*args).astimezone().isoformat()


def test_stamps_user_and_assistant_turns():
    stamped = stamp_messages(
        [
            {'role': 'user', 'content': 'leaving in 20', 'createdAt': _iso(2026, 7, 27, 19, 5)},
            {'role': 'assistant', 'content': 'have fun', 'createdAt': _iso(2026, 7, 27, 19, 6)},
        ],
        NOW,
    )
    assert stamped == [
        {'role': 'user', 'content': '[today 19:05] leaving in 20'},
        {'role': 'assistant', 'content': '[today 19:06] have fun'},
    ]


def test_labels_older_turns_by_day():
    stamped = stamp_messages(
        [
            {'role': 'user', 'content': 'a', 'createdAt': _iso(2026, 7, 26, 23, 30)},
            {'role': 'user', 'content': 'b', 'createdAt': _iso(2026, 7, 20, 9, 0)},
        ],
        NOW,
    )
    assert stamped[0]['content'] == '[yesterday 23:30] a'
    assert stamped[1]['content'] == '[Jul 20 09:00] b'


@pytest.mark.parametrize('created_at', [None, '', 'not-a-date', 12345])
def test_untimestamped_messages_pass_through(created_at):
    # The voice listener keeps its history in memory with no timestamps.
    stamped = stamp_messages([{'role': 'user', 'content': 'hi', 'createdAt': created_at}], NOW)
    assert stamped == [{'role': 'user', 'content': 'hi'}]


def test_system_messages_are_never_stamped():
    stamped = stamp_messages(
        [{'role': 'system', 'content': 'be nice', 'createdAt': _iso(2026, 7, 27, 19, 5)}],
        NOW,
    )
    assert stamped == [{'role': 'system', 'content': 'be nice'}]


def test_format_now_context():
    assert format_now_context(NOW) == 'Right now it is Monday, 27 July 2026, 21:58.'


@pytest.fixture
def captured(monkeypatch):
    """Runs chat_stream against a stub provider, returning the outgoing messages."""
    seen = {}

    def fake_stream(messages, **kwargs):
        seen['messages'] = messages
        yield 'ok'

    monkeypatch.setattr('backend.ai.chat.get_provider_config', lambda: {'ollama_model': 'm'})
    monkeypatch.setattr('backend.ai.chat.default_generation_opts', dict)
    monkeypatch.setattr('backend.ai.chat._native_chat_stream', fake_stream)
    return seen


def test_chat_stream_stamps_and_explains_the_prefix(captured):
    list(chat_stream([{'role': 'user', 'content': 'hi', 'createdAt': _iso(2026, 7, 27, 19, 5)}]))
    system, user = captured['messages']

    assert system['role'] == 'system'
    assert SYSTEM_PROMPT in system['content']
    assert 'Right now it is ' in system['content']
    assert TIME_PREFIX_NOTE in system['content']
    # chat_stream stamps against the real clock, so pin the shape, not the day
    # (stamp_messages covers the exact labels).
    assert re.fullmatch(r'\[[^\]]+ \d{2}:\d{2}\] hi', user['content'])


def test_chat_stream_skips_the_note_without_timestamps(captured):
    list(chat_stream([{'role': 'user', 'content': 'hi'}]))
    system, user = captured['messages']

    # Nothing to explain, so the prompt stays lean — but the clock still helps.
    assert TIME_PREFIX_NOTE not in system['content']
    assert 'Right now it is ' in system['content']
    assert user == {'role': 'user', 'content': 'hi'}


def test_chat_stream_keeps_caller_prompt_and_rag(captured):
    list(chat_stream([{'role': 'user', 'content': 'hi'}], 'RAG BLOCK', 'CALLER PROMPT'))
    system = captured['messages'][0]['content']

    assert 'CALLER PROMPT' in system
    assert 'RAG BLOCK' in system
    assert SYSTEM_PROMPT not in system


def test_time_context_can_be_switched_off(captured):
    # Transcript cleanup demands an exact output shape; a stray clock line in
    # its prompt is only noise.
    list(chat_stream(
        [{'role': 'user', 'content': 'hi', 'createdAt': _iso(2026, 7, 27, 19, 5)}],
        system_prompt='OUTPUT ONLY THE TEXT',
        with_time_context=False,
    ))
    system, user = captured['messages']

    assert system['content'] == 'OUTPUT ONLY THE TEXT'
    # Also normalized to role/content — createdAt must never reach the provider.
    assert user == {'role': 'user', 'content': 'hi'}
