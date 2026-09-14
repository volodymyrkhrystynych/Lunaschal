"""The one-time catch-up for recordings that predate the titling rules.

Two backlogs, and they overlap only by accident: clips that were never
described, and entries carrying a recording that were never titled. The pass
does descriptions first so that a title written in the second half has the
description from the first in hand.

What matters here is the same thing that matters in the curated-tag scan: a
model that cannot answer must stop the pass rather than finish it. "No
description" and "the GPU was paused" are not the same answer, and the second
one recorded as the first is a wrong answer that looks finished.
"""
import io
import time

import pytest

from backend.ai import service
from backend.db.connection import get_db
from backend.journal import backfill
from backend.routes import journal as journal_routes


@pytest.fixture(autouse=True)
def _isolated_media_root(tmp_path, monkeypatch):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'journal-media'))


@pytest.fixture(autouse=True)
def _quiet_progress():
    """The registry is module state; a test that leaves a run in it would make
    the next one's `start()` return False."""
    yield
    backfill._progress.clear()
    backfill._progress['running'] = False


def _record(client, **columns):
    """One entry made the way the bottom bar's Record button makes it."""
    res = client.post(
        '/api/journal/recordings',
        data={'file': (io.BytesIO(b'\x00' * 32), 'recording.webm', 'audio/webm')},
        content_type='multipart/form-data',
    ).get_json()
    if columns:
        db = get_db()
        db.execute(
            'UPDATE journal_attachments SET '
            + ', '.join(f'{c} = ?' for c in columns) + ' WHERE id = ?',
            (*columns.values(), res['attachment']['id']),
        )
        db.commit()
    return res['id'], res['attachment']['id']


def _run_now():
    """Run the pass on this thread, so the assertions are not racing it."""
    backfill._progress.update(
        {'running': True, 'phase': 'describing', 'processed': 0, 'total': 0,
         'described': 0, 'titled': 0, 'failed': 0, 'stopped': None}
    )
    backfill._run()
    return backfill.status()


def _title(client, entry_id):
    return client.get(f'/api/journal/{entry_id}').get_json().get('title')


# --- what it finds -----------------------------------------------------------

def test_it_counts_both_backlogs(client):
    entry_id, _ = _record(client)

    counts = backfill.counts()
    assert counts['undescribed'] == 1
    assert counts['untitled'] == 1


def test_a_described_clip_is_not_counted_again(client):
    _record(client, description_status='done', description='Rain on a window.')

    assert backfill.counts()['undescribed'] == 0


def test_a_failed_description_is_counted(client):
    """Every one of the existing errors is a call against a model alias that no
    longer exists. Skipping them would preserve a misconfiguration rather than
    fix it."""
    _record(client, description_status='error', description_error='model not found')

    assert backfill.counts()['undescribed'] == 1


def test_a_titled_entry_is_left_alone(client, monkeypatch):
    entry_id, _ = _record(client)
    get_db().execute(
        "UPDATE journal_entries SET title='Already named' WHERE id=?", (entry_id,)
    )
    get_db().commit()

    assert backfill.counts()['untitled'] == 0


def test_a_text_entry_with_no_recording_is_not_this_passs_business(client):
    """An untitled entry with nothing but words is the ordinary titling path's
    problem. This pass only knows things about recordings."""
    client.post('/api/journal', json={'content': 'A day.', 'pendingAttachments': 1})

    assert backfill.counts()['untitled'] == 0


# --- what it does ------------------------------------------------------------

def test_it_describes_then_titles(client, monkeypatch, run_jobs_sync):
    """Order is the point: a title written before the description exists is a
    title generated from nothing, which is the state these entries are already
    in."""
    entry_id, _ = _record(client)
    monkeypatch.setattr(journal_routes, '_do_attachment_audio_description',
                        lambda _p, _n: 'Heavy rain and a passing train.')
    seen = []

    def fake(content, context=None):
        seen.append(context)
        return {'title': 'Rain on the walk home', 'tags': ['memory']}

    monkeypatch.setattr(journal_routes, 'generate_journal_metadata', fake)

    result = _run_now()

    assert result['described'] == 1
    assert result['titled'] == 1
    assert result['stopped'] is None
    assert 'Heavy rain' in seen[-1]
    assert _title(client, entry_id) == 'Rain on the walk home'


def test_a_clip_whose_file_is_gone_is_skipped_not_failed(client, monkeypatch):
    """The row outlived the file. There is nothing to describe and nothing that
    went wrong — counting it as a failure would make a tidy backlog look broken
    forever."""
    _record(client)
    monkeypatch.setattr(journal_routes, '_resolve_attachment_path', lambda _p: None)
    monkeypatch.setattr(journal_routes, 'generate_journal_metadata',
                        lambda content, context=None: {})

    result = _run_now()

    assert result['described'] == 0
    assert result['failed'] == 0
    assert result['stopped'] is None


def test_one_bad_clip_does_not_end_the_pass(client, monkeypatch, run_jobs_sync):
    _record(client)
    _record(client)
    calls = []

    def flaky(_p, _n):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('one bad clip')
        return 'A quiet room.'

    monkeypatch.setattr(journal_routes, '_do_attachment_audio_description', flaky)
    monkeypatch.setattr(journal_routes, 'generate_journal_metadata',
                        lambda content, context=None: {'title': 'T', 'tags': []})

    result = _run_now()

    assert result['failed'] == 1
    assert result['described'] == 1
    assert result['stopped'] is None


# --- when the model cannot answer --------------------------------------------

def test_a_paused_model_stops_the_describe_half(client, monkeypatch):
    """Not "this clip has no description" — the clip was never asked about. The
    remaining rows stay in the backlog for the next run."""
    _record(client)
    _record(client)

    def paused(_p, _n):
        raise service.InferencePaused('inference is paused')

    monkeypatch.setattr(journal_routes, '_do_attachment_audio_description', paused)

    result = _run_now()

    assert result['running'] is False
    assert result['stopped']
    assert result['described'] == 0
    # Both clips are still there to describe.
    assert backfill.counts()['undescribed'] == 2


def test_a_paused_model_stops_the_titling_half(client, monkeypatch, run_jobs_sync):
    """`_generate_metadata_bg` swallows every exception so a failed title cannot
    break the entry it was titling — so a paused GPU arrives here as a silent
    no-op. The thread mark is the signal that survives that."""
    _record(client, description_status='done', description='Rain.')
    _record(client, description_status='done', description='Rain.')

    def paused(content, context=None):
        service._mark_deferred('paused')
        return {}

    monkeypatch.setattr(journal_routes, 'generate_journal_metadata', paused)

    result = _run_now()

    assert result['stopped'] == 'paused'
    assert result['titled'] == 0
    assert backfill.counts()['untitled'] == 2


# --- the routes --------------------------------------------------------------

def test_the_status_route_reports_both_counts(client):
    _record(client)

    body = client.get('/api/journal/backfill/recordings').get_json()

    assert body['undescribed'] == 1
    assert body['untitled'] == 1
    assert body['progress']['running'] is False


def test_a_second_run_is_refused_while_one_is_going(client, monkeypatch):
    monkeypatch.setattr(backfill, '_run', lambda: time.sleep(0.2))
    assert client.post('/api/journal/backfill/recordings').status_code == 202

    assert client.post('/api/journal/backfill/recordings').status_code == 409
