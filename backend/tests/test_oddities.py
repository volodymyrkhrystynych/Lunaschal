"""The pure half of the server-oddities pass: reading a journal into kinds of
problem. Fixtures are shaped after a real day of `lunaschal.service`, where
every record is priority 6 and a traceback is forty records long."""
import json
from datetime import datetime

from backend.ops import oddities as od
from backend.ops.journal_logs import parse_journal_json

T0 = int(datetime(2026, 10, 15, 12, 0).timestamp())


def _entries(*messages, start=T0, priority=6):
    return [
        {'ts': start + i, 'priority': priority, 'identifier': 'run-prod.sh', 'message': m}
        for i, m in enumerate(messages)
    ]


FLASK_TRACEBACK = (
    '[2026-10-15 23:38:52,244] ERROR in app: Exception on /api/fanfic/browser/poll [POST]',
    'Traceback (most recent call last):',
    '  File "/home/u/Lunaschal/.venv/lib/python3.14/site-packages/flask/app.py", line 1511, in wsgi_app',
    '    response = self.full_dispatch_request()',
    '  File "/home/u/Lunaschal/backend/routes/fanfic.py", line 676, in browser_poll',
    "    result = browser.poll(_browser_body().get('clientId'))",
    # A request thread logging mid-traceback.
    '100.95.99.65 - - [15/Oct/2026 23:38:52] "GET /api/torrents/status HTTP/1.1" 200 -',
    '  File "/home/u/Lunaschal/backend/fanfic/pacing.py", line 23, in state',
    "    row = get_db().execute('SELECT * FROM fanfic_site_limits').fetchone()",
    '          ~~~~~~~~~~~~~~~~^^^^^^^^^^^^',
    '  File "/usr/lib/python3.14/sqlite3/dbapi2.py", line 9, in execute',
    '    pass',
    'sqlite3.InterfaceError: bad parameter or other API misuse',
)

DISCONNECT_TRACEBACK = (
    'Error on request:',
    'Traceback (most recent call last):',
    '  File "/x/.venv/lib/python3.14/site-packages/werkzeug/serving.py", line 335, in execute',
    '    write(data)',
    'BrokenPipeError: [Errno 32] Broken pipe',
    'During handling of the above exception, another exception occurred:',
    'Traceback (most recent call last):',
    '  File "/x/.venv/lib/python3.14/site-packages/werkzeug/serving.py", line 371, in run_wsgi',
    '    execute(self.server.app)',
    'ConnectionResetError: [Errno 104] Connection reset by peer',
)


# --- normalise ----------------------------------------------------------


def test_normalise_folds_ids_paths_and_counts():
    a = od.normalise('Audio description failed for window 0:00–0:30 of '
                     '/home/u/Lunaschal/data/journal/01K7TAE5VWPMWBNQ7VND/attachment.m4a: '
                     'proxy error')
    b = od.normalise('Audio description failed for window 1:30–2:00 of '
                     '/srv/data/journal/01K7BKYNQ2JES1WNNM4DNBNZAB/attachment.m4a: proxy error')
    assert a == b
    assert '<path>' in a and 'N:N–N:N' in a


def test_normalise_keeps_digits_inside_names():
    assert 'sqlite3' in od.normalise('sqlite3.InterfaceError after 12 tries')
    assert 'after N tries' in od.normalise('sqlite3.InterfaceError after 12 tries')


def test_normalise_drops_echoed_payloads():
    a = od.normalise("""Life-wiki write failed for health: model returned non-JSON content: '{\\n "title": "health", "summary": "They train three times'""")
    b = od.normalise("""Life-wiki write failed for health: model returned non-JSON content: '{\\n "title": "health", "summary": "Something else entirely here'""")
    assert a == b
    assert "content: '…'" in a


def test_normalise_folds_commit_hashes():
    a = od.normalise('deploy-check: skip-failed (branch=main local=5a1fc remote=e9a1e93fb)')
    b = od.normalise('deploy-check: skip-failed (branch=main local=77be0 remote=0c4d2aa81)')
    assert a == b
    assert 'local=<hex> remote=<hex>' in a
    # A count and an English word are left alone.
    assert od.normalise('retried 12345 times, deadbeef') == 'retried N times, deadbeef'


def test_normalise_strips_the_logging_prefix():
    assert od.normalise('[2026-10-15 23:38:52,244] ERROR in app: boom') == 'ERROR in app: boom'


# --- classify -----------------------------------------------------------


def test_severity_comes_from_the_text_not_priority():
    assert od.classify('cpu call describe_audio failed after 0.4s', 6) == od.ERROR
    assert od.classify('UserWarning: resource_tracker: leaked semaphores', 6) == od.WARNING
    assert od.classify('Sync log compacted: 4 superseded versions removed', 6) is None
    # A PRIORITY that is set still counts.
    assert od.classify('something odd', 3) == od.ERROR
    assert od.classify('something odd', 4) == od.WARNING


def test_known_noise_is_not_an_oddity():
    assert od.classify('WARNING: This is a development server. Do not use it in a '
                       'production deployment. Use a production WSGI server instead.') is None


# --- extract_events -----------------------------------------------------


def test_a_traceback_is_one_event_with_its_headline_and_our_last_frame():
    events = od.extract_events(_entries(*FLASK_TRACEBACK), 'lunaschal')
    assert len(events) == 1
    e = events[0]
    assert e['kind'] == od.TRACEBACK
    assert e['frame'] == {'file': 'backend/fanfic/pacing.py', 'basename': 'pacing.py',
                          'line': 23, 'function': 'state'}
    assert e['template'] == ('sqlite3.InterfaceError in backend/fanfic/pacing.py:state: '
                             'bad parameter or other API misuse')
    assert e['headline'].endswith('Exception on /api/fanfic/browser/poll [POST]')
    assert 'ERROR in app' in e['sample'] and 'InterfaceError' in e['sample']
    assert 'HTTP/1.1' not in e['sample']


def test_a_traceback_has_the_same_fingerprint_from_any_checkout():
    elsewhere = tuple(m.replace('/home/u/Lunaschal', '/srv/worktree') for m in FLASK_TRACEBACK)
    a = od.extract_events(_entries(*FLASK_TRACEBACK), 'lunaschal')[0]
    b = od.extract_events(_entries(*elsewhere), 'lunaschal')[0]
    assert a['fingerprint'] == b['fingerprint']


def test_client_disconnect_tracebacks_are_dropped_and_whatever_follows_is_kept():
    events = od.extract_events(
        _entries(*DISCONNECT_TRACEBACK, 'Audio description failed: proxy error'),
        'lunaschal',
    )
    assert [e['template'] for e in events] == ['Audio description failed: proxy error']


def test_a_chained_traceback_with_a_real_cause_is_kept():
    lines = DISCONNECT_TRACEBACK[:5] + (
        'During handling of the above exception, another exception occurred:',
        'Traceback (most recent call last):',
        '  File "/x/backend/routes/journal.py", line 40, in save',
        '    db.commit()',
        'KeyError: \'id\'',
    )
    events = od.extract_events(_entries(*lines), 'lunaschal')
    assert len(events) == 1
    assert events[0]['template'].startswith('KeyError in backend/routes/journal.py:save')


def test_a_traceback_with_no_frame_of_ours_falls_back_to_the_last_frame():
    lines = (
        'Traceback (most recent call last):',
        '  File "/usr/lib/python3.14/threading.py", line 1041, in _bootstrap_inner',
        '    self.run()',
        'RuntimeError: dictionary changed size during iteration',
    )
    events = od.extract_events(_entries(*lines), 'lunaschal')
    assert events[0]['template'].startswith('RuntimeError in threading.py:_bootstrap_inner')
    assert events[0]['frame'] is None


def test_the_indented_half_of_a_warning_is_not_an_event():
    events = od.extract_events(_entries(
        '/usr/lib/python3.14/multiprocessing/resource_tracker.py:301: UserWarning: '
        'resource_tracker: There appear to be 1 leaked semaphore objects',
        '  warnings.warn(',
    ), 'lunaschal')
    assert [e['kind'] for e in events] == [od.WARNING]


def test_events_survive_a_round_trip_through_journal_json():
    """journald stores a line with ANSI codes as a list of byte values."""
    raw = '\n'.join(json.dumps(r) for r in [
        {'__REALTIME_TIMESTAMP': str(T0 * 1_000_000), 'PRIORITY': '6',
         'MESSAGE': list(b'\x1b[31mcpu call describe_audio failed after 0.4s\x1b[0m')},
        {'__REALTIME_TIMESTAMP': str((T0 + 1) * 1_000_000), 'PRIORITY': '6',
         'MESSAGE': '100.95.99.65 - - [15/Oct/2026 07:29:59] "GET /api/x HTTP/1.1" 500 -'},
    ])
    events = od.extract_events(parse_journal_json(raw), 'lunaschal')
    assert len(events) == 1
    assert events[0]['ts'] == T0
    assert events[0]['template'] == 'cpu call describe_audio failed after Ns'


# --- group --------------------------------------------------------------


def test_group_counts_per_fingerprint_and_per_day_keeping_the_newest_sample():
    day2 = T0 + 86400
    events = (
        od.extract_events(_entries('cpu call describe_audio failed after 0.4s'), 'lunaschal')
        + od.extract_events(_entries('cpu call describe_audio failed after 1.2s',
                                     start=day2), 'lunaschal')
        + od.extract_events(_entries('Ignoring padding error'), 'lunaschal')
    )
    clusters = od.group(events)
    assert [c['count'] for c in clusters] == [2, 1]
    top = clusters[0]
    assert top['first_ts'] == T0 and top['last_ts'] == day2
    assert top['sample'].endswith('1.2s')
    assert sorted(top['days'].values()) == [1, 1] and len(top['days']) == 2


def test_failed_llm_jobs_become_events():
    rows = [
        {'kind': 'journal.polish', 'error': 'model returned 3 empty choices', 'finished_at': T0},
        {'kind': 'journal.polish', 'error': 'model returned 7 empty choices', 'finished_at': T0 + 5},
        {'kind': 'food.structure', 'error': None, 'finished_at': T0},
    ]
    clusters = od.group(od.llm_job_events(rows))
    assert [(c['unit'], c['count']) for c in clusters] == [('llm_jobs', 2), ('llm_jobs', 1)]
    assert clusters[0]['template'] == 'journal.polish: model returned N empty choices'


def test_scan_argv_is_an_absolute_window():
    argv = od.scan_argv('lunaschal.service', 100, 200)
    assert argv[:4] == ['journalctl', '--user', '-u', 'lunaschal.service']
    assert argv[-4:] == ['--since', '@100', '--until', '@200']


# --- status -------------------------------------------------------------


def test_status_when_seen():
    assert od.status_when_seen(None, 1) == od.NEW
    assert od.status_when_seen(od.NEW, 1) == od.NEW          # a second sighting the same day
    assert od.status_when_seen(od.NEW, 2) == od.RECURRING
    assert od.status_when_seen(od.RECURRING, 1) == od.RECURRING
    assert od.status_when_seen(od.QUIET, 1) == od.REGRESSED
    assert od.status_when_seen(od.REGRESSED, 2) == od.RECURRING


def test_status_when_unseen_goes_quiet_after_a_week():
    now = T0 + 30 * 86400
    assert od.status_when_unseen(od.RECURRING, now - 3 * 86400, now) == od.RECURRING
    assert od.status_when_unseen(od.RECURRING, now - 8 * 86400, now) == od.QUIET
    assert od.status_when_unseen(od.QUIET, now - 20 * 86400, now) == od.QUIET
