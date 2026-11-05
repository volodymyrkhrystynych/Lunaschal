"""The server-oddities pass end to end: journal (mocked journalctl) and failed
llm_jobs in, rows and statuses out, a note per new problem, and the section the
briefing ends with."""
import json
from datetime import datetime

import pytest
from ulid import ULID

from backend import oddities_job as job
from backend.ai import oddities as ai
from backend.db.connection import get_db
from backend.ops import oddities as od

DAY = 86400
NOW = int(datetime(2026, 10, 15, 5, 30).timestamp())


def _record(ts, message, priority=6):
    return json.dumps({'__REALTIME_TIMESTAMP': str(ts * 1_000_000),
                       'PRIORITY': str(priority), 'MESSAGE': message})


class _Journal(dict):
    def __init__(self):
        super().__init__()
        self.calls = []


@pytest.fixture
def journal(monkeypatch):
    """Per-service journal text; a service missing from the dict reads as
    "no journal here" (None), like a dev box with no user bus."""
    logs = _Journal()
    calls = logs.calls

    def fake(argv):
        calls.append(argv)
        service = argv[argv.index('-u') + 1]
        since = int(argv[argv.index('--since') + 1][1:])
        until = int(argv[argv.index('--until') + 1][1:])
        if service not in logs:
            return None
        return '\n'.join(
            line for line in logs[service]
            if since <= int(json.loads(line)['__REALTIME_TIMESTAMP']) // 1_000_000 < until
        )

    monkeypatch.setattr(job, '_journalctl', fake)
    logs.update({info['service']: [] for info in job.UNITS.values()})
    return logs


@pytest.fixture
def notes(monkeypatch):
    """A model that notes whatever it is given, recording each call."""
    calls = []

    def annotate(items):
        calls.append([i['fingerprint'] for i in items])
        return {i['fingerprint']: f"note for {i['template'][:20]}" for i in items}

    monkeypatch.setattr(ai, 'annotate', annotate)
    monkeypatch.setattr('backend.ai.provider.is_ai_configured', lambda: True)
    return calls


def _rows():
    return {r['template']: dict(r) for r in get_db().execute('SELECT * FROM server_oddities')}


def _fail_job(kind, error, finished_at):
    get_db().execute(
        'INSERT INTO llm_jobs (id, kind, status, error, created_at, finished_at)'
        " VALUES (?, ?, 'error', ?, ?, ?)",
        (str(ULID()), kind, error, finished_at, finished_at),
    )
    get_db().commit()


AUDIO = 'cpu call describe_audio failed after 0.4s: proxy error'
AUDIO_T = 'cpu call describe_audio failed after Ns: proxy error'


def test_a_pass_records_journal_and_llm_job_problems(journal, notes):
    journal['lunaschal.service'] += [_record(NOW - 3600, AUDIO), _record(NOW - 1800, AUDIO),
                                     _record(NOW - 600, 'Sync log compacted: 3 removed')]
    journal['lunaschal-llama.service'].append(_record(NOW - 900, 'srv  load_model: failed to load'))
    _fail_job('journal.polish', 'model returned an empty response', NOW - 100)

    result = job.run_oddities_pass(NOW)

    assert result['journalAvailable'] is True
    assert result['clusters'] == 3 and result['events'] == 4
    rows = _rows()
    assert rows[AUDIO_T]['last_scan_count'] == 2 and rows[AUDIO_T]['status'] == od.NEW
    assert rows[AUDIO_T]['unit'] == 'lunaschal'
    assert rows['journal.polish: model returned an empty response']['unit'] == 'llm_jobs'
    assert all(r['note'] for r in rows.values())
    scan = get_db().execute('SELECT * FROM server_oddity_scans').fetchone()
    assert (scan['since_ts'], scan['until_ts']) == (NOW - job.DEFAULT_WINDOW, NOW)


def test_the_next_scan_starts_where_the_last_ended_and_never_counts_twice(journal, notes):
    journal['lunaschal.service'].append(_record(NOW - 600, AUDIO))
    job.run_oddities_pass(NOW)
    job.run_oddities_pass(NOW + 60)  # a rerun sees nothing new
    row = _rows()[AUDIO_T]
    assert row['total_count'] == 1 and row['last_scan_count'] == 0
    assert journal.calls[-1][-3] == f'@{NOW}'


def test_a_long_gap_is_capped_rather_than_reported_as_one_day(journal, notes):
    job.run_oddities_pass(NOW - 10 * DAY)
    job.run_oddities_pass(NOW)
    since = get_db().execute(
        'SELECT since_ts FROM server_oddity_scans ORDER BY until_ts DESC LIMIT 1'
    ).fetchone()[0]
    assert since == NOW - job.MAX_WINDOW


def test_status_moves_new_recurring_quiet_regressed(journal, notes):
    day = lambda n: NOW + n * DAY  # noqa: E731
    journal['lunaschal.service'].append(_record(day(0) - 60, AUDIO))
    journal['lunaschal.service'].append(_record(day(1) - 60, AUDIO))
    journal['lunaschal.service'].append(_record(day(12) - 60, AUDIO))

    job.run_oddities_pass(day(0))
    assert _rows()[AUDIO_T]['status'] == od.NEW
    job.run_oddities_pass(day(1))
    assert _rows()[AUDIO_T]['status'] == od.RECURRING
    first_note = _rows()[AUDIO_T]['note']

    for n in range(2, 10):
        job.run_oddities_pass(day(n))
    assert _rows()[AUDIO_T]['status'] == od.QUIET

    notes.clear()
    # Two nights missed: the window is capped at MAX_WINDOW, which still reaches it.
    job.run_oddities_pass(day(12))
    row = _rows()[AUDIO_T]
    assert row['status'] == od.REGRESSED
    # The one time a note is rewritten.
    assert notes == [[row['fingerprint']]]
    assert row['note'] == first_note  # same text from the fake, but freshly written:
    assert row['note_written_at'] == day(12)


def test_a_note_is_written_once(journal, notes):
    journal['lunaschal.service'] += [_record(NOW - 60, AUDIO), _record(NOW + DAY - 60, AUDIO)]
    job.run_oddities_pass(NOW)
    job.run_oddities_pass(NOW + DAY)
    assert len(notes) == 1
    assert _rows()[AUDIO_T]['note_written_at'] == NOW


def test_a_model_failure_costs_only_the_notes(journal, monkeypatch):
    monkeypatch.setattr('backend.ai.provider.is_ai_configured', lambda: True)

    def down(items):
        raise RuntimeError('llama-server unreachable')

    monkeypatch.setattr(ai, 'annotate', down)
    journal['lunaschal.service'].append(_record(NOW - 60, AUDIO))
    result = job.run_oddities_pass(NOW)
    assert result['notes'] == 0
    assert _rows()[AUDIO_T]['note'] is None and _rows()[AUDIO_T]['total_count'] == 1


def test_no_journal_still_scans_background_jobs(monkeypatch, notes):
    monkeypatch.setattr(job, '_journalctl', lambda argv: None)
    _fail_job('food.structure', 'boom', NOW - 100)
    result = job.run_oddities_pass(NOW)
    assert result['journalAvailable'] is False and result['clusters'] == 1


# --- the briefing section ------------------------------------------------


def test_report_says_when_the_check_did_not_run():
    report = job.briefing_report(NOW)
    assert report == {'ran': False, 'journalAvailable': False, 'items': [], 'more': 0}
    assert "didn't run" in job.render_markdown(report)


def test_report_on_a_quiet_day_is_one_line(journal, notes):
    job.run_oddities_pass(NOW)
    text = job.render_markdown(job.briefing_report(NOW + 60))
    assert text == '**Server oddities:** none in the past day.'


def test_report_on_a_quiet_day_without_a_journal_says_what_was_not_checked(monkeypatch, notes):
    monkeypatch.setattr(job, '_journalctl', lambda argv: None)
    job.run_oddities_pass(NOW)
    text = job.render_markdown(job.briefing_report(NOW))
    assert text.startswith('**Server oddities:** none in the past day.')
    assert "journal couldn't be read" in text


def test_report_lists_regressed_then_new_then_recurring_and_caps(journal, notes):
    db = get_db()
    for i, status in enumerate([od.RECURRING, od.NEW, od.REGRESSED] + [od.NEW] * 4):
        db.execute(
            'INSERT INTO server_oddities (fingerprint, unit, kind, template, sample, status,'
            ' total_count, last_scan_count, note, first_seen, last_seen)'
            ' VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (f'fp{i}', 'lunaschal', 'error', f'problem `{i}`', 's', status, 9, 10 - i,
             'a note' if i == 0 else None, NOW - DAY, NOW - 60),
        )
    db.execute('INSERT INTO server_oddity_days VALUES (?,?,?)', ('fp0', '2026-10-13', 1))
    db.execute('INSERT INTO server_oddity_days VALUES (?,?,?)', ('fp0', '2026-10-14', 1))
    db.execute('INSERT INTO server_oddity_scans VALUES (?,?,?,?,?,?)',
               ('s1', NOW - DAY, NOW, 1, 70, NOW))
    db.commit()

    report = job.briefing_report(NOW)
    assert [i['fingerprint'] for i in report['items']] == ['fp2', 'fp1', 'fp3', 'fp4', 'fp5']
    assert report['more'] == 2
    text = job.render_markdown(report)
    assert text.splitlines()[0] == '**Server oddities (past day)**'
    assert "- **Back** · lunaschal · 8× — `problem '2'`" in text
    assert '- …and 2 more.' in text
    assert 'journal' not in text

    report['items'] = [i for i in job._current(db, NOW) if i['fingerprint'] == 'fp0']
    assert '**Recurring** (2 of 7 days)' in job.render_markdown(report)
    assert '\n  a note' in job.render_markdown(report)


# --- the model call ------------------------------------------------------


def test_annotate_keeps_only_known_ids_and_trims(monkeypatch):
    captured = {}

    def fake_chat_json(prompt, system=None, schema=None, **kw):
        captured.update(prompt=prompt, schema=schema)
        return {'notes': [
            {'id': 'aaa', 'note': '  Likely the CPU model is down.\n  Check the router. '},
            {'id': 'zzz', 'note': 'invented'},
            {'id': 'aaa', 'note': 'duplicate'},
        ]}

    monkeypatch.setattr(ai, 'chat_json', fake_chat_json)
    item = {'fingerprint': 'aaa', 'unit': 'lunaschal', 'kind': 'traceback', 'status': 'new',
            'count': 3, 'days': 1, 'template': 'X in backend/ai/oddities.py:annotate',
            'sample': 'Traceback…',
            'frame': {'file': 'backend/ai/oddities.py', 'line': 10, 'function': 'annotate'}}
    assert ai.annotate([item]) == {'aaa': 'Likely the CPU model is down. Check the router.'}
    assert captured['schema']['properties']['notes']['items']['properties']['id']['enum'] == ['aaa']
    # The frame's own source is shown to the model.
    assert 'source around backend/ai/oddities.py:10' in captured['prompt']
    assert '>   10  ' in captured['prompt']


def test_snippet_refuses_paths_outside_the_repo():
    assert ai._snippet({'file': '../../etc/passwd', 'line': 1}) is None
    assert ai._snippet({'file': 'backend/nope.py', 'line': 1}) is None
