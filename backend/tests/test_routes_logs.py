"""backend/routes/logs.py: the Settings → Logs endpoint.

`_journalctl` is monkeypatched in every test — there is no user systemd bus
under pytest, and the parsing itself is covered by test_ops_journal_logs.py.
"""

import json

import pytest

from backend.routes import logs as logs_routes


def _journal_line(msg: str, *, priority='6', ts='1787795677000000') -> str:
    return json.dumps({
        '__REALTIME_TIMESTAMP': ts, 'PRIORITY': priority,
        'SYSLOG_IDENTIFIER': 'run-prod.sh', 'MESSAGE': msg,
    })


def test_reads_and_parses_lines_in_order(client, monkeypatch):
    payload = '\n'.join([_journal_line('first'), _journal_line('second')])
    monkeypatch.setattr(logs_routes, '_journalctl', lambda argv: payload)

    data = client.get('/api/logs?unit=lunaschal').get_json()
    assert data['available'] is True
    assert data['unit'] == 'lunaschal'
    assert [e['message'] for e in data['entries']] == ['first', 'second']
    assert data['note'] is None


def test_unknown_unit_is_rejected(client, monkeypatch):
    monkeypatch.setattr(logs_routes, '_journalctl', lambda argv: '')
    resp = client.get('/api/logs?unit=rm-rf')
    assert resp.status_code == 400
    assert 'Unknown unit' in resp.get_json()['error']


def test_unavailable_journal_degrades_gracefully(client, monkeypatch):
    monkeypatch.setattr(logs_routes, '_journalctl', lambda argv: None)
    data = client.get('/api/logs?unit=lunaschal').get_json()
    assert data['available'] is False
    assert data['entries'] == []
    assert data['note']


def test_lines_param_is_clamped_in_the_argv(client, monkeypatch):
    seen = {}

    def spy(argv):
        seen['argv'] = argv
        return ''

    monkeypatch.setattr(logs_routes, '_journalctl', spy)
    client.get('/api/logs?unit=lunaschal&lines=99999')
    argv = seen['argv']
    assert argv[argv.index('-n') + 1] == '2000'


def test_since_and_priority_flow_through(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(logs_routes, '_journalctl',
                        lambda argv: seen.setdefault('argv', argv) and '')
    client.get('/api/logs?unit=lunaschal&since=1h&priority=4')
    argv = seen['argv']
    assert '--since' in argv and argv[argv.index('-p') + 1] == '4'


def test_units_endpoint_lists_the_allowlist(client, monkeypatch):
    monkeypatch.setattr(logs_routes, '_systemctl', lambda *a: 'LoadState=loaded\n')
    rows = client.get('/api/logs/units').get_json()
    ids = {r['id'] for r in rows}
    assert ids == {'lunaschal', 'lunaschal-llama', 'lunaschal-deploy', 'lunaschal-backup'}
    assert all(isinstance(r['available'], bool) for r in rows)
    assert all(r['label'] for r in rows)


def test_units_endpoint_marks_unknown_units_unavailable(client, monkeypatch):
    monkeypatch.setattr(logs_routes, '_systemctl', lambda *a: None)
    rows = client.get('/api/logs/units').get_json()
    assert all(r['available'] is False for r in rows)
