"""A paused job must come back, even when its handler swallowed the refusal.

`InferencePaused` and `Preempted` mean "run this again later". They are also
ordinary `Exception`s, and most of the functions the job worker calls wrap their
model call in `except Exception` on purpose, so that a failed enrichment can
never break the row it was enriching. The two facts together lost an evening of
screenshot captions: each was recorded as a permanent failure on the attachment,
each handler returned normally, and the worker marked every job `done` — so
resuming the GPU found an empty queue.

What is pinned here is that the queue no longer takes a handler's word for it.
"""
import io

import pytest
from PIL import Image
from ulid import ULID

from backend.ai import jobs, service
from backend.db import connection
from backend.db.connection import get_db


@pytest.fixture(autouse=True)
def clean(client):
    service.reset()
    yield
    service.reset()


@pytest.fixture
def router(monkeypatch):
    def fake_post(path, body, timeout=10.0):
        return True, None

    from backend.routes import settings as settings_routes
    monkeypatch.setattr(settings_routes, '_router_post', fake_post)


def _pause(client):
    client.post('/api/settings/inference/pause')
    service.invalidate_pause_cache()


def _resume(client):
    client.post('/api/settings/inference/resume')
    service.invalidate_pause_cache()


def _job(job_id):
    return get_db().execute('SELECT * FROM llm_jobs WHERE id=?', (job_id,)).fetchone()


def _caption_job(attachment_id):
    row = get_db().execute(
        "SELECT id FROM llm_jobs WHERE kind='journal.transcribe_attachment'"
        ' AND target_id=?', (attachment_id,)).fetchone()
    return row['id'] if row else None


# --------------------------------------------------------------- the backstop

def test_a_handler_that_swallows_a_pause_leaves_its_job_pending(client, router):
    """The regression. This handler is written exactly the way the real ones
    are, and used to produce a `done` row with the work never done."""
    recorded = []

    @jobs.handler('test.swallows_the_pause')
    def _swallow(target_id, payload):
        try:
            with service.slot(lane=service.GPU, label='swallowed'):
                pass
        except Exception as e:                      # noqa: BLE001 — the point
            recorded.append(str(e))

    _pause(client)
    job_id = jobs.enqueue('test.swallows_the_pause', 'shot-1')

    jobs.drain_once()

    assert recorded, 'the handler really did swallow it'
    row = _job(job_id)
    assert row['status'] == 'pending'
    assert row['error'] is None
    assert row['cancels'] == 0


def test_a_handler_that_swallows_a_preemption_requeues_and_counts_the_cancel(client):
    # Raised from inside the slot, because that is where the service marks it:
    # a handler that invents a `Preempted` from nowhere has nothing to find,
    # and nothing in the app does that.
    @jobs.handler('test.swallows_a_real_preemption')
    def _swallow(target_id, payload):
        try:
            with service.slot(lane=service.GPU, label='preempted'):
                raise service.Preempted('cancelled for an interactive call')
        except Exception:                           # noqa: BLE001 — the point
            pass

    job_id = jobs.enqueue('test.swallows_a_real_preemption', 'shot-2')
    jobs.drain_once()

    row = _job(job_id)
    assert row['status'] == 'pending'
    assert row['cancels'] == 1
    assert row['error'] is None


def test_a_handler_that_did_its_work_is_still_marked_done(client, router):
    """No false positives: the mark is per-thread and must not survive the job
    that set it, or one paused job would requeue every job after it."""
    @jobs.handler('test.swallows_then_recovers')
    def _swallow(target_id, payload):
        try:
            with service.slot(lane=service.GPU, label='swallowed'):
                pass
        except Exception:                           # noqa: BLE001
            pass

    @jobs.handler('test.plain_work')
    def _plain(target_id, payload):
        pass

    _pause(client)
    paused_id = jobs.enqueue('test.swallows_then_recovers', 'a')
    jobs.drain_once()
    assert _job(paused_id)['status'] == 'pending'

    _resume(client)
    plain_id = jobs.enqueue('test.plain_work', 'b')
    jobs.drain_once(exclude={paused_id})

    assert _job(plain_id)['status'] == 'done'


def test_a_swallowed_pause_sets_the_job_aside_so_other_work_keeps_draining(
        client, router):
    """A requeued GPU job must not be retried forever at the head of the queue,
    or the CPU lane starves behind it for the whole pause."""
    @jobs.handler('test.aside_gpu')
    def _gpu(target_id, payload):
        try:
            with service.slot(lane=service.GPU, label='gpu'):
                pass
        except Exception:                           # noqa: BLE001
            pass

    ran = []

    @jobs.handler('test.aside_cpu')
    def _cpu(target_id, payload):
        with service.slot(lane=service.CPU, label='cpu'):
            ran.append(target_id)

    _pause(client)
    gpu_id = jobs.enqueue('test.aside_gpu', 'gpu-1')
    jobs.enqueue('test.aside_cpu', 'cpu-1')

    aside = set()
    jobs.drain_once(aside)          # the GPU job, refused and set aside
    jobs.drain_once(aside)          # the CPU job, which must not be blocked

    assert gpu_id in aside
    assert ran == ['cpu-1']
    assert _job(gpu_id)['status'] == 'pending'


# ------------------------------------------------- the path that lost the work

def _photo(client, entry_id):
    img = Image.new('RGB', (8, 8), (120, 120, 120))
    buf = io.BytesIO()
    img.save(buf, 'JPEG')
    return client.post(
        f'/api/journal/{entry_id}/attachments',
        data={'file': (io.BytesIO(buf.getvalue()), 'shot.jpg', 'image/jpeg')},
        content_type='multipart/form-data',
    ).get_json()


@pytest.fixture
def vision_on_the_card(client):
    """Captioning as this install actually runs it.

    `_repoint_vision_at_qwen36` moves the vision alias onto the chat model, so
    a photo caption is GPU-lane work and the pause switch does stop it. The
    original design assumed captions were CPU-only and therefore immune.
    """
    db = get_db()
    db.execute("UPDATE settings SET llama_vision_model='qwen36'")
    db.commit()


@pytest.fixture(autouse=True)
def _media_root(tmp_path, monkeypatch):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'journal-media'))


def test_a_photo_caption_refused_by_the_pause_is_not_recorded_as_a_failure(
        client, router, vision_on_the_card):
    entry_id = client.post('/api/journal', json={'content': 'A day.'}).get_json()['id']
    _pause(client)
    attachment = _photo(client, entry_id)

    job_id = _caption_job(attachment['id'])
    assert job_id is not None, 'the upload queued a caption'

    # Run this job rather than whatever `drain_once` finds first: creating the
    # entry queues its own polish and metadata, and the assertion is about the
    # caption.
    jobs.process_one(job_id)

    assert _job(job_id)['status'] == 'pending'
    row = get_db().execute(
        'SELECT transcript_status, transcript_error FROM journal_attachments'
        ' WHERE id=?', (attachment['id'],)).fetchone()
    assert row['transcript_status'] != 'error'
    assert row['transcript_error'] is None


def test_the_caption_lands_after_a_resume(client, router, vision_on_the_card,
                                          monkeypatch):
    entry_id = client.post('/api/journal', json={'content': 'A day.'}).get_json()['id']
    _pause(client)
    attachment = _photo(client, entry_id)
    job_id = _caption_job(attachment['id'])
    jobs.process_one(job_id)
    assert _job(job_id)['status'] == 'pending', 'held, not lost'

    from backend.ai import images as images_ai
    monkeypatch.setattr(images_ai, 'caption_image',
                        lambda path, hint=None: 'A terminal, mid-command.')
    _resume(client)
    jobs.process_one(job_id)

    row = get_db().execute(
        'SELECT transcript, transcript_status FROM journal_attachments WHERE id=?',
        (attachment['id'],)).fetchone()
    assert row['transcript'] == 'A terminal, mid-command.'
    assert row['transcript_status'] == 'done'


# ------------------------------------------------------------------ the repair

def _strand(entry_id, error='GPU inference is paused'):
    """An attachment in the state the old worker left behind."""
    db = get_db()
    attachment_id = str(ULID())
    db.execute(
        'INSERT INTO journal_attachments (id, entry_id, kind, name, path, mime,'
        ' size, position, transcript_status, transcript_error, created_at)'
        " VALUES (?,?,'image','shot','journal/x/shot.jpg','image/jpeg',10,0,"
        "'error',?,0)",
        (attachment_id, entry_id, error),
    )
    db.commit()
    return attachment_id


def _rerun_the_repair(db):
    db.execute('ALTER TABLE settings DROP COLUMN paused_jobs_requeued')
    db.commit()
    connection._requeue_jobs_lost_to_a_pause(db)


def test_attachments_stranded_by_the_old_bug_are_requeued(client):
    entry_id = client.post('/api/journal', json={'content': 'A day.'}).get_json()['id']
    attachment_id = _strand(entry_id)

    db = get_db()
    _rerun_the_repair(db)

    job = db.execute(
        "SELECT * FROM llm_jobs WHERE kind='journal.transcribe_attachment'"
        ' AND target_id=?', (attachment_id,)).fetchone()
    assert job is not None and job['status'] == 'pending'

    row = db.execute(
        'SELECT transcript_status, transcript_error FROM journal_attachments'
        ' WHERE id=?', (attachment_id,)).fetchone()
    assert row['transcript_status'] == 'idle'
    assert row['transcript_error'] is None


def test_an_attachment_that_failed_for_a_real_reason_keeps_its_error(client):
    """The repair is matched on the wording, not on the status: a caption that
    failed because the file was missing still wants its Transcribe button and
    its explanation, not a silent requeue."""
    entry_id = client.post('/api/journal', json={'content': 'A day.'}).get_json()['id']
    attachment_id = _strand(entry_id, error='The image file is missing')

    db = get_db()
    _rerun_the_repair(db)

    assert db.execute(
        'SELECT COUNT(*) n FROM llm_jobs WHERE target_id=?',
        (attachment_id,)).fetchone()['n'] == 0
    assert db.execute(
        'SELECT transcript_status FROM journal_attachments WHERE id=?',
        (attachment_id,)).fetchone()['transcript_status'] == 'error'


def test_the_repair_is_latched_and_does_not_run_twice(client):
    entry_id = client.post('/api/journal', json={'content': 'A day.'}).get_json()['id']
    attachment_id = _strand(entry_id)

    db = get_db()
    _rerun_the_repair(db)
    # Strand it again; the latch is set, so nothing should touch it.
    db.execute("UPDATE journal_attachments SET transcript_status='error',"
               " transcript_error='GPU inference is paused' WHERE id=?",
               (attachment_id,))
    db.commit()
    connection._requeue_jobs_lost_to_a_pause(db)

    assert db.execute(
        'SELECT transcript_status FROM journal_attachments WHERE id=?',
        (attachment_id,)).fetchone()['transcript_status'] == 'error'


# ------------------------------------------------------- the other vision path

def test_a_chat_photo_refused_by_the_pause_is_held_rather_than_failed(
        client, router, monkeypatch, tmp_path, run_jobs_sync):
    """Chat photos go through the same `llama_vision_model` as journal captions,
    so `_repoint_vision_at_qwen36` puts them on the GPU lane too — and a pause
    used to leave the composer saying the model refused to read a picture it was
    never shown."""
    monkeypatch.setenv('CHAT_ROOT', str(tmp_path / 'chat'))

    from backend.routes import chat as chat_routes
    monkeypatch.setattr(chat_routes, '_do_read_attachment', _refuse)

    conv = client.post('/api/chat/conversations', json={}).get_json()['id']
    img = io.BytesIO()
    Image.new('RGB', (8, 8), (120, 120, 120)).save(img, 'JPEG')
    [att] = client.post(
        f'/api/chat/conversations/{conv}/attachments',
        data={'image': (io.BytesIO(img.getvalue()), 'photo.jpg')},
        content_type='multipart/form-data',
    ).get_json()

    row = get_db().execute(
        'SELECT description_status, description_error FROM chat_attachments'
        ' WHERE id=?', (att['id'],)).fetchone()
    assert row['description_status'] != 'error'
    assert row['description_error'] is None

    job = get_db().execute(
        "SELECT status FROM llm_jobs WHERE kind='chat.read_attachment'"
        ' AND target_id=?', (att['id'],)).fetchone()
    assert job['status'] == 'pending'


def _refuse(path):
    """What `describe_image` does when the GPU lane is off."""
    service._mark_deferred('paused')
    raise service.InferencePaused('GPU inference is paused')
