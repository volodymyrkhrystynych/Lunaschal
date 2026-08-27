"""backend/ops/journal_logs.py: the pure parser + argv builder behind
Settings → Logs. No subprocess, no clock dependence beyond the local tz."""

import json
from datetime import datetime

import pytest

from backend.ops import journal_logs as jl


def _line(**fields) -> str:
    return json.dumps(fields)


def test_parse_plain_string_message():
    ts_us = 1_787_795_677_320_108
    text = _line(
        __REALTIME_TIMESTAMP=str(ts_us),
        PRIORITY='6',
        SYSLOG_IDENTIFIER='run-prod.sh',
        MESSAGE='newspapers sync: already-saved 2026-08-25',
    )
    (entry,) = jl.parse_journal_json(text)
    assert entry['message'] == 'newspapers sync: already-saved 2026-08-25'
    assert entry['priority'] == 6
    assert entry['identifier'] == 'run-prod.sh'
    expected = datetime.fromtimestamp(ts_us / 1_000_000).astimezone().isoformat()
    assert entry['ts'] == expected


def test_parse_byte_array_message_decodes_and_strips_ansi():
    # journald hands back MESSAGE as a list of byte values when the line has
    # non-UTF-8 bytes; werkzeug colourises its access log with ANSI codes.
    raw = b'\x1b[36mGET /api/health\x1b[0m 200'
    text = _line(
        __REALTIME_TIMESTAMP='1787795677000000',
        MESSAGE=list(raw),
    )
    (entry,) = jl.parse_journal_json(text)
    assert entry['message'] == 'GET /api/health 200'
    assert '\x1b' not in entry['message']


def test_parse_missing_priority_defaults_to_info():
    text = _line(__REALTIME_TIMESTAMP='1787795677000000', MESSAGE='hi')
    (entry,) = jl.parse_journal_json(text)
    assert entry['priority'] == jl.DEFAULT_PRIORITY


def test_parse_skips_blank_and_malformed_lines():
    good = _line(__REALTIME_TIMESTAMP='1787795677000000', MESSAGE='kept')
    text = '\n'.join(['', 'not json', '{bad', good, '   '])
    entries = jl.parse_journal_json(text)
    assert [e['message'] for e in entries] == ['kept']


def test_parse_bad_timestamp_yields_none_ts():
    text = _line(__REALTIME_TIMESTAMP='not-a-number', MESSAGE='x')
    (entry,) = jl.parse_journal_json(text)
    assert entry['ts'] is None


def test_build_argv_clamps_lines():
    assert '-n' in jl.build_argv('lunaschal.service', lines=99999)
    argv = jl.build_argv('lunaschal.service', lines=99999)
    assert argv[argv.index('-n') + 1] == str(jl.MAX_LINES)
    argv = jl.build_argv('lunaschal.service', lines=0)
    assert argv[argv.index('-n') + 1] == str(jl.MIN_LINES)
    argv = jl.build_argv('lunaschal.service', lines='garbage')
    assert argv[argv.index('-n') + 1] == str(jl.DEFAULT_LINES)


def test_build_argv_priority_only_when_in_range():
    assert '-p' not in jl.build_argv('u.service', priority=None)
    assert '-p' not in jl.build_argv('u.service', priority=9)
    assert '-p' not in jl.build_argv('u.service', priority='x')
    argv = jl.build_argv('u.service', priority=3)
    assert argv[argv.index('-p') + 1] == '3'


def test_build_argv_since_only_for_known_preset():
    assert '--since' not in jl.build_argv('u.service', since='whenever')
    assert '--since' not in jl.build_argv('u.service', since=None)
    argv = jl.build_argv('u.service', since='1h')
    assert argv[argv.index('--since') + 1] == jl.SINCE_PRESETS['1h']


def test_build_argv_shape():
    argv = jl.build_argv('lunaschal.service', lines=10)
    assert argv[:8] == [
        'journalctl', '--user', '-u', 'lunaschal.service',
        '-o', 'json', '--no-pager', '-n',
    ]


@pytest.mark.parametrize('msg,expected', [
    ('100.95.99.65 - - [26/Aug/2026 21:54:37] "GET /api/journal HTTP/1.1" 200 -', True),
    ('127.0.0.1 - - [26/Aug/2026 21:54:37] "POST /api/chat/stream HTTP/1.1" 500 -', True),
    ('newspapers sync failed: ConnectionError', False),
    ('', False),
])
def test_looks_like_access_log(msg, expected):
    assert jl.looks_like_access_log(msg) is expected
