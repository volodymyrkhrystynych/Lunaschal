"""The switch that frees the card, and what it does to work in flight.

Two halves, and both are needed: the flag is what stops the app admitting GPU
work, and the unload is what actually hands the VRAM back. Unloading without
the flag would be undone within seconds — the router loads on demand, so the
next queued call naming the alias pulls the model straight back on.
"""
import json
import time

import pytest

from backend.ai import jobs, service
from backend.db.connection import get_db


@pytest.fixture(autouse=True)
def clean(client):
    service.reset()
    yield
    service.reset()


@pytest.fixture
def router(monkeypatch):
    """Record what we POST at llama-server's router."""
    calls = []

    def fake_post(path, body, timeout=10.0):
        calls.append((path, body))
        return True, None

    from backend.routes import settings as settings_routes
    monkeypatch.setattr(settings_routes, '_router_post', fake_post)
    return calls


def _paused(client):
    return client.get('/api/settings/inference').get_json()


def _entry_with_raw_text(client, text='A day.'):
    """An entry the Polish button can actually act on.

    `/polish` reads `raw_content` and 400s without one, so a plain create is
    not enough — polish exists to reconcile a transcript against a polished
    version, and an entry that was typed has nothing to reconcile.
    """
    from backend.db.connection import get_db

    entry_id = client.post('/api/journal', json={'content': text}).get_json()['id']
    db = get_db()
    db.execute('UPDATE journal_entries SET raw_content=? WHERE id=?', (text, entry_id))
    db.commit()
    return entry_id


# ------------------------------------------------------------------ the switch

def test_pause_sets_the_flag_and_unloads_the_chat_model(client, router):
    body = client.post('/api/settings/inference/pause').get_json()

    assert body['paused'] is True
    assert body['pausedSince'] is not None
    assert router == [('/models/unload', {'model': 'qwen36'})]


def test_resume_clears_the_flag_and_does_not_reload(client, router):
    client.post('/api/settings/inference/pause')
    router.clear()

    body = client.post('/api/settings/inference/resume').get_json()

    assert body['paused'] is False
    assert body['pausedSince'] is None
    # Pulling 22 GB back onto the card because a button was pressed is a
    # surprise, not a service — the next real request reloads it lazily.
    assert router == []


def test_a_router_that_does_not_answer_still_leaves_us_paused(client, monkeypatch):
    """The flag is the state that matters; the unload is an optimisation."""
    from backend.routes import settings as settings_routes
    monkeypatch.setattr(settings_routes, '_router_post',
                        lambda *a, **k: (False, 'Connection refused'))

    body = client.post('/api/settings/inference/pause').get_json()

    assert body['paused'] is True
    assert body['unloaded'] is False
    assert 'refused' in body['unloadError']


def test_the_pause_survives_a_restart(client, router):
    """A gaming evening outlives at least one Flask reload."""
    client.post('/api/settings/inference/pause')
    service.invalidate_pause_cache()

    row = get_db().execute('SELECT inference_paused FROM settings').fetchone()
    assert row['inference_paused'] == 1
    assert service.is_paused() is True


def test_the_flag_is_reported_by_the_settings_endpoint(client, router):
    assert client.get('/api/settings').get_json()['inferencePaused'] is False
    client.post('/api/settings/inference/pause')
    assert client.get('/api/settings').get_json()['inferencePaused'] is True


# --------------------------------------------------------------------- the gate

def test_a_paused_gpu_lane_refuses_and_the_cpu_lane_does_not(client, router):
    client.post('/api/settings/inference/pause')
    service.invalidate_pause_cache()

    with pytest.raises(service.InferencePaused):
        with service.slot(lane=service.GPU, label='chat'):
            pass

    # Photo captions and embeddings are CPU-resident; blocking them would buy
    # the card nothing and cost the user the features that still work.
    with service.slot(lane=service.CPU, label='caption'):
        pass


def test_the_lane_is_chosen_by_alias_not_by_feature(client, router):
    """images.py can be repointed at the chat alias, and must then be gated."""
    assert service.lane_for('qwen36') == service.GPU
    assert service.lane_for('gemma4-12b-omni') == service.CPU
    assert service.lane_for('embed') == service.CPU


# ------------------------------------------------------------------- the queue

def test_a_queued_job_stays_pending_while_paused(client, router):
    from backend.ai import job_handlers  # noqa: F401

    @jobs.handler('test.paused')
    def _refuses(target_id, payload):
        raise service.InferencePaused('GPU inference is paused')

    job_id = jobs.enqueue('test.paused', 'target-1')
    result = jobs.process_one(job_id)

    assert result['error'] == 'paused'
    row = get_db().execute('SELECT * FROM llm_jobs WHERE id=?', (job_id,)).fetchone()
    assert row['status'] == 'pending'
    # Not counted as a cancel: being paused is not the job's fault and must not
    # push it toward the non-preemptible escalation.
    assert row['cancels'] == 0


def test_a_gpu_job_attempted_while_paused_stays_queued(client, router):
    """The drain deliberately does not short-circuit on the pause flag.

    Checking it there would stop the CPU lane too (see the lane test below), so
    a GPU job is attempted, refused by `slot()`, and put straight back.
    """
    @jobs.handler('test.reaches_the_gpu')
    def _gpu(target_id, payload):
        with service.slot(lane=service.GPU, label='gpu-job'):
            pass

    client.post('/api/settings/inference/pause')
    service.invalidate_pause_cache()
    job_id = jobs.enqueue('test.reaches_the_gpu', 'event-1')

    jobs.drain_once()

    row = get_db().execute('SELECT * FROM llm_jobs WHERE id=?', (job_id,)).fetchone()
    assert row['status'] == 'pending'
    assert row['error'] is None
    assert jobs.pending_count() == 1


def test_a_job_that_never_reaches_the_model_still_finishes_while_paused(client, router):
    """The gate is at the request, not at the job. Work that resolves without
    asking the model — a classifier that finds no row, an early return — has
    nothing to be refused and should not be held up."""
    ran = []

    @jobs.handler('test.no_model_call')
    def _quick(target_id, payload):
        ran.append(target_id)

    client.post('/api/settings/inference/pause')
    service.invalidate_pause_cache()
    job_id = jobs.enqueue('test.no_model_call', 'x')

    jobs.drain_once()

    assert ran == ['x']
    row = get_db().execute('SELECT * FROM llm_jobs WHERE id=?', (job_id,)).fetchone()
    assert row['status'] == 'done'


def test_a_preempted_job_is_requeued_and_counted(client):
    @jobs.handler('test.preempted')
    def _preempted(target_id, payload):
        raise service.Preempted('cancelled for an interactive call')

    job_id = jobs.enqueue('test.preempted', 'target-2')
    jobs.process_one(job_id)

    row = get_db().execute('SELECT * FROM llm_jobs WHERE id=?', (job_id,)).fetchone()
    assert row['status'] == 'pending'
    assert row['cancels'] == 1


def test_a_job_stops_yielding_once_it_has_been_cancelled_enough(client):
    """Otherwise a busy day could starve one job for the whole day."""
    seen = []

    @jobs.handler('test.records_preemptible')
    def _record(target_id, payload):
        seen.append(service.current_preemptible())

    job_id = jobs.enqueue('test.records_preemptible', 'target-3')
    get_db().execute('UPDATE llm_jobs SET cancels=? WHERE id=?',
                     (jobs.MAX_CANCELS, job_id))
    get_db().commit()
    jobs.process_one(job_id)

    assert seen == [False]


def test_a_real_failure_is_recorded_rather_than_retried_forever(client):
    @jobs.handler('test.explodes')
    def _boom(target_id, payload):
        raise RuntimeError('the model said no')

    job_id = jobs.enqueue('test.explodes', 'target-4')
    jobs.process_one(job_id)

    row = get_db().execute('SELECT * FROM llm_jobs WHERE id=?', (job_id,)).fetchone()
    assert row['status'] == 'error'
    assert 'the model said no' in row['error']


def test_the_same_work_is_not_queued_twice(client):
    first = jobs.enqueue('calendar.classify', 'event-9')
    second = jobs.enqueue('calendar.classify', 'event-9')

    assert first is not None
    assert second is None
    assert jobs.pending_count() == 1


def test_a_finished_job_does_not_block_queueing_the_same_work_again(client):
    first = jobs.enqueue('calendar.classify', 'event-10')
    get_db().execute("UPDATE llm_jobs SET status='done' WHERE id=?", (first,))
    get_db().commit()

    assert jobs.enqueue('calendar.classify', 'event-10') is not None


def test_an_orphaned_running_job_goes_back_in_the_queue(client):
    """A job mid-flight when the process died must not stay `running` forever."""
    from backend.db import connection

    job_id = jobs.enqueue('calendar.classify', 'event-11')
    db = get_db()
    db.execute("UPDATE llm_jobs SET status='running' WHERE id=?", (job_id,))
    db.commit()

    connection._reset_stale_llm_jobs(db)

    row = db.execute('SELECT * FROM llm_jobs WHERE id=?', (job_id,)).fetchone()
    assert row['status'] == 'pending'
    assert row['started_at'] is None


def test_a_kind_with_no_handler_is_recorded_not_retried(client):
    job_id = jobs.enqueue('test.vanished', 'target-5')
    jobs.process_one(job_id)

    row = get_db().execute('SELECT * FROM llm_jobs WHERE id=?', (job_id,)).fetchone()
    assert row['status'] == 'error'
    assert 'No handler' in row['error']


def test_the_payload_round_trips_through_the_row(client):
    """The whole reason a job is data: it has to survive the process."""
    got = {}

    @jobs.handler('test.payload')
    def _capture(target_id, payload):
        got['target'] = target_id
        got['payload'] = payload

    job_id = jobs.enqueue('test.payload', 'target-6', {'text': 'two eggs', 'n': 3})
    jobs.process_one(job_id)

    assert got == {'target': 'target-6', 'payload': {'text': 'two eggs', 'n': 3}}


# ------------------------------------------------------ the handler registry
#
# A job is data, and the mapping back to code is the one thing that can rot
# without anything noticing: an enqueue writes a row whatever happens, and a
# handler that cannot be imported surfaces as `status='error'` on the row long
# after the change that broke it. These two tests are the guard rail.

def test_every_enqueued_kind_has_a_handler():
    """A kind with no handler is recorded as a permanent error, never retried."""
    import pathlib
    import re

    from backend.ai import job_handlers  # noqa: F401

    root = pathlib.Path(__file__).resolve().parents[2] / 'backend'
    enqueued = set()
    for path in root.rglob('*.py'):
        if 'tests' in path.parts:
            continue
        for match in re.finditer(r"jobs\.enqueue\(\s*'([a-z_.]+)'", path.read_text()):
            enqueued.add(match.group(1))

    assert enqueued, 'found no enqueue call sites — the scan is broken, not the code'
    assert enqueued <= jobs.known_kinds(), (
        f'enqueued with no handler: {sorted(enqueued - jobs.known_kinds())}'
    )


def test_every_handler_can_actually_reach_its_function():
    """Handlers import lazily, so a moved function is invisible until it runs.

    `structure_food_entry` shipped briefly pointing at backend.ai.food, where it
    has never lived — the job queued fine and failed only on the worker.
    """
    import importlib
    import inspect
    import re

    from backend.ai import job_handlers  # noqa: F401

    broken = []
    for kind in sorted(jobs.known_kinds()):
        if kind.startswith('test.'):
            continue  # registered by other tests in this file
        for line in inspect.getsource(jobs._HANDLERS[kind]).splitlines():
            match = re.match(r'\s*from ([\w.]+) import ([\w, ]+)', line)
            if not match:
                continue
            module_name, names = match.group(1), match.group(2)
            try:
                module = importlib.import_module(module_name)
            except Exception as e:
                broken.append(f'{kind}: cannot import {module_name} ({e})')
                continue
            for name in (n.strip() for n in names.split(',')):
                if name and not hasattr(module, name):
                    broken.append(f'{kind}: {module_name} has no {name}')

    assert not broken, broken


# ------------------------------------------------- the pause is lane-shaped

def test_cpu_work_keeps_draining_while_the_gpu_is_paused(client, router):
    """Pausing frees the card. It must not stop work that never used it.

    The worker sets a GPU job aside for the rest of the pause rather than
    retrying it, so it walks past to the photo captions and embeddings that can
    still run — otherwise one queued polish would stall the whole queue.
    """
    ran = []

    @jobs.handler('test.needs_gpu')
    def _gpu(target_id, payload):
        with service.slot(lane=service.GPU, label='gpu-job'):
            ran.append('gpu')

    @jobs.handler('test.cpu_only')
    def _cpu(target_id, payload):
        with service.slot(lane=service.CPU, label='cpu-job'):
            ran.append('cpu')

    # The GPU job is queued first, so a worker that stopped at it would never
    # reach the second one.
    jobs.enqueue('test.needs_gpu', 'g1')
    jobs.enqueue('test.cpu_only', 'c1')

    client.post('/api/settings/inference/pause')
    service.invalidate_pause_cache()

    deferred: set[str] = set()
    jobs.drain_once(deferred)   # the GPU one: refused and set aside
    jobs.drain_once(deferred)   # the CPU one: runs

    assert ran == ['cpu']
    # The CPU job finished; the GPU one is back in the queue rather than failed.
    gpu_row = get_db().execute(
        "SELECT * FROM llm_jobs WHERE kind='test.needs_gpu'").fetchone()
    assert gpu_row['status'] == 'pending'
    assert gpu_row['error'] is None
    assert jobs.pending_count() == 1


def test_the_deferred_gpu_job_runs_after_a_resume(client, router):
    ran = []

    @jobs.handler('test.deferred_then_runs')
    def _gpu(target_id, payload):
        with service.slot(lane=service.GPU, label='gpu-job'):
            ran.append(target_id)

    jobs.enqueue('test.deferred_then_runs', 'g2')
    client.post('/api/settings/inference/pause')
    service.invalidate_pause_cache()

    deferred: set[str] = set()
    jobs.drain_once(deferred)
    assert ran == []
    assert deferred, 'the paused job should have been set aside'

    client.post('/api/settings/inference/resume')
    service.invalidate_pause_cache()
    deferred.clear()            # what the worker loop does on resume
    jobs.drain_once(deferred)

    assert ran == ['g2']
    assert jobs.pending_count() == 0


# --------------------------------------------------- how a refusal surfaces

def test_a_paused_call_answers_503_with_a_flag_the_ui_can_read(client, router,
                                                               monkeypatch):
    """One error handler, rather than a try/except in forty routes.

    503 and not 500 because the model is deliberately off — a temporary state
    the caller can fix — and the flag is what lets the UI offer Resume instead
    of rendering a stack trace.
    """
    from backend.routes import journal as journal_routes

    def _paused(*a, **k):
        raise service.InferencePaused('GPU inference is paused')

    entry_id = _entry_with_raw_text(client)
    monkeypatch.setattr(journal_routes, 'polish_journal_entry', _paused)

    r = client.post(f'/api/journal/{entry_id}/polish')

    assert r.status_code == 503
    body = r.get_json()
    assert body['inferencePaused'] is True
    assert 'paused' in body['error'].lower()


def test_nothing_the_user_wrote_is_lost_when_a_call_is_refused(client, router,
                                                              monkeypatch):
    from backend.routes import journal as journal_routes

    entry_id = _entry_with_raw_text(client, 'Worth keeping.')
    monkeypatch.setattr(journal_routes, 'polish_journal_entry',
                        lambda *a, **k: (_ for _ in ()).throw(
                            service.InferencePaused('paused')))

    client.post(f'/api/journal/{entry_id}/polish')

    entry = client.get(f'/api/journal/{entry_id}').get_json()
    assert entry['content'] == 'Worth keeping.'


def test_the_refusal_wording_is_shared_with_the_sse_frames(client):
    """Two places say this; they must not drift into saying different things."""
    from backend.routes.chat import PAUSED_MESSAGE as chat_message
    from backend.routes.ideas import PAUSED_MESSAGE as ideas_message

    assert chat_message == ideas_message == service.PAUSED_MESSAGE


def test_a_paused_polish_is_flagged_while_a_broken_one_is_not(client, router,
                                                              monkeypatch):
    """Both are 503, but only one of them has a button that fixes it.

    `polish_journal_entry` deliberately wraps *every* model failure in
    `PolishUnavailable`, because the voice-draft pipeline depends on that to
    degrade to raw text — a clip recorded during a pause must still become an
    entry. So the paused case is carried as a flag on the same exception
    rather than as a different type.
    """
    from backend.ai.journal import PolishUnavailable
    from backend.routes import journal as journal_routes

    entry_id = _entry_with_raw_text(client)

    monkeypatch.setattr(journal_routes, 'polish_journal_entry',
                        lambda *a, **k: (_ for _ in ()).throw(
                            PolishUnavailable('GPU inference is paused', paused=True)))
    body = client.post(f'/api/journal/{entry_id}/polish').get_json()
    assert body['inferencePaused'] is True

    monkeypatch.setattr(journal_routes, 'polish_journal_entry',
                        lambda *a, **k: (_ for _ in ()).throw(
                            PolishUnavailable('model returned an empty polish')))
    body = client.post(f'/api/journal/{entry_id}/polish').get_json()
    assert 'inferencePaused' not in body
    assert 'empty polish' in body['error']


def test_the_wrapper_marks_a_paused_failure_at_the_source(client, monkeypatch):
    """The flag has to be set where the exception is wrapped, or the route
    below it cannot tell the two 503s apart."""
    from backend.ai import journal as journal_ai
    from backend.ai.journal import PolishUnavailable

    monkeypatch.setattr(journal_ai, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(journal_ai, 'chat_text', lambda *a, **k: (_ for _ in ()).throw(
        service.InferencePaused('GPU inference is paused')))

    with pytest.raises(PolishUnavailable) as caught:
        journal_ai.polish_journal_entry('some raw text')
    assert caught.value.paused is True

    monkeypatch.setattr(journal_ai, 'chat_text', lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError('Connection refused')))
    with pytest.raises(PolishUnavailable) as caught:
        journal_ai.polish_journal_entry('some raw text')
    assert caught.value.paused is False
