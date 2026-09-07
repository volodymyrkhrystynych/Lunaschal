"""What the service records about itself, and where that surfaces.

The pause switch made this necessary rather than nice. Before it, a model call
that did not happen was rare enough to be a bug; now "nothing ran" is a normal
state with several different causes — paused, preempted, queued behind an
interactive burst, or genuinely broken — and they are indistinguishable from
the outside. Each one gets its own event kind so the Logs tab can tell them
apart, and the counters say whether it is happening once or all day.
"""
import pytest

from backend.ai import jobs, service


@pytest.fixture(autouse=True)
def clean(client):
    service.reset()
    yield
    service.reset()


@pytest.fixture
def router(monkeypatch):
    from backend.routes import settings as settings_routes
    monkeypatch.setattr(settings_routes, '_router_post',
                        lambda path, body, timeout=10.0: (True, None))


def _kinds(events):
    return [e['kind'] for e in events]


def _first(events, kind):
    for e in events:
        if e['kind'] == kind:
            return e
    raise AssertionError(f'no {kind} event in {_kinds(events)}')


# ------------------------------------------------------------- the call record

def test_a_completed_call_is_recorded_with_its_lane_and_timings():
    with service.slot(lane=service.CPU, label='chat.photo'):
        pass

    event = _first(service.recent_events(), 'call')
    assert event['label'] == 'chat.photo'
    assert event['lane'] == service.CPU
    assert event['priority'] == 1
    assert event['outcome'] == 'ok'
    # Wait and run are separate fields on purpose: a nine-second reply looks
    # the same whether the model was slow or the lane was busy, and only these
    # two apart can say which.
    assert event['ran'] >= 0
    assert event['waited'] >= 0
    assert service.counters()['calls'] == 1


def test_a_failing_call_records_the_exception_and_counts_as_an_error():
    with pytest.raises(ValueError):
        with service.slot(lane=service.CPU, label='embed.card'):
            raise ValueError('llama-server said no')

    event = _first(service.recent_events(), 'call')
    assert event['outcome'] == 'error'
    assert 'ValueError' in event['detail']
    assert 'llama-server said no' in event['detail']
    assert service.counters()['errors'] == 1


def test_a_background_priority_is_recorded_as_such():
    with service.background():
        with service.slot(lane=service.CPU, label='journal.metadata'):
            pass
    assert _first(service.recent_events(), 'call')['priority'] == 2


def test_events_come_back_newest_first_and_honour_the_limit():
    for i in range(5):
        with service.slot(lane=service.CPU, label=f'call-{i}'):
            pass

    events = service.recent_events(limit=2)
    assert len(events) == 2
    assert [e['label'] for e in events] == ['call-4', 'call-3']


def test_the_ring_buffer_does_not_grow_without_bound(monkeypatch):
    # The buffer is capped at construction, so this proves the cap rather than
    # the number: an unbounded log on a per-model-call path is a slow leak.
    for i in range(service.EVENT_LIMIT + 10):
        service._record('call', label=f'x{i}', outcome='ok')
    assert len(service.recent_events(limit=10_000)) == service.EVENT_LIMIT


# ------------------------------------------------------------------ preemption

def test_preemption_records_which_interactive_call_took_the_lane():
    import threading

    started = threading.Event()
    release = threading.Event()

    def background_call():
        with service.background():
            with service.slot(lane=service.CPU, label='journal.polish'):
                started.set()
                release.wait(5)

    t = threading.Thread(target=background_call, daemon=True)
    t.start()
    assert started.wait(5)

    with service.slot(lane=service.CPU, label='chat.reply'):
        pass
    release.set()
    t.join(5)

    event = _first(service.recent_events(), 'preempt')
    assert event['label'] == 'journal.polish'
    # Naming the arriving call is the whole value of the line: "polish was
    # cancelled" is a mystery, "cancelled for chat" is an explanation.
    assert event['by'] == 'chat.reply'
    assert service.counters()['preempted'] == 1


# --------------------------------------------------------------- while paused

def test_a_refusal_while_paused_is_recorded_rather_than_silent(client, router):
    client.post('/api/settings/inference/pause')
    service.reset()  # drop the pause event so the refusal is the only one left

    with pytest.raises(service.InferencePaused):
        with service.slot(lane=service.GPU, label='chat.reply'):
            pass

    event = _first(service.recent_events(), 'refused')
    assert event['label'] == 'chat.reply'
    assert event['lane'] == service.GPU
    assert service.counters()['refused'] == 1


def test_pausing_and_resuming_write_themselves_into_the_log(client, router):
    client.post('/api/settings/inference/pause')
    assert 'pause' in _kinds(service.recent_events())

    client.post('/api/settings/inference/resume')
    kinds = _kinds(service.recent_events())
    assert kinds[0] == 'resume'


# ------------------------------------------------------------------ the route

def test_the_activity_endpoint_returns_both_halves(client, router):
    with service.slot(lane=service.CPU, label='chat.photo'):
        pass
    jobs.enqueue('journal.polish', 'entry-1')

    body = client.get('/api/settings/inference/activity').get_json()

    assert [e['label'] for e in body['events'] if e['kind'] == 'call'] == ['chat.photo']
    assert body['counters']['calls'] == 1
    assert body['jobCounts']['pending'] == 1
    queued = body['jobs'][0]
    assert queued['kind'] == 'journal.polish'
    assert queued['targetId'] == 'entry-1'
    assert queued['status'] == 'pending'
    # The registry ships with the response so a job whose handler was renamed
    # shows up as exactly that, rather than as a row that mysteriously never
    # runs.
    assert 'journal.polish' in body['handlers']
    assert 'gpu' in body['lanes'] and 'cpu' in body['lanes']


def test_the_activity_limit_is_clamped_to_the_buffer(client):
    body = client.get('/api/settings/inference/activity?limit=99999').get_json()
    assert len(body['events']) <= service.EVENT_LIMIT
    # Junk in the query string must not 500 a debugging page.
    assert client.get('/api/settings/inference/activity?limit=abc').status_code == 200


def test_a_failed_job_keeps_its_message_where_the_panel_can_show_it(client):
    """The half of the log that outlives a restart.

    A ring buffer answers "what is happening now". This answers "why is
    yesterday's entry still unpolished", which is the question that actually
    gets asked, and it can only be answered by a row.
    """
    @jobs.handler('test.explodes')
    def _explodes(target_id, payload):
        raise RuntimeError('the model refused')

    job_id = jobs.enqueue('test.explodes', 'entry-9')
    jobs.process_one(job_id)

    body = client.get('/api/settings/inference/activity').get_json()
    row = next(j for j in body['jobs'] if j['id'] == job_id)
    assert row['status'] == 'error'
    assert row['error'] == 'the model refused'
    assert row['attempts'] == 1


def test_a_closed_stream_is_abandoned_rather_than_an_error():
    """`chat_stream_events` holds its slot across the whole generator.

    An SSE client that navigates away throws `GeneratorExit` into it, which is
    the ordinary end of a chat the user walked away from. Counting those as
    failures would put a red line in the log for the most common thing that
    happens in the app, and train the eye to ignore the ones that matter.
    """
    def streaming():
        with service.slot(lane=service.CPU, label='chat_stream'):
            yield 'first'
            yield 'second'

    gen = streaming()
    next(gen)
    gen.close()

    event = _first(service.recent_events(), 'call')
    assert event['outcome'] == 'abandoned'
    assert service.counters()['errors'] == 0
