"""Titling an entry that has photos coming.

Attachments necessarily arrive *after* the entry — they need its id — so the
title used to be generated from the text alone, milliseconds after the create,
long before any photo had been captioned. `pendingAttachments` on the create
request is the client saying "n files are on their way"; the metadata job then
waits for them to land and settle before asking for a title.

Two things this has to get right, and one it has to survive:

- It must not deadlock. The captioning jobs run on the job queue's single
  shared worker, so the wait cannot itself be a queued job — it would head-of-line
  block the very work it is waiting for.
- It must not wait forever on an upload that never comes (a failed request, a
  closed tab). The cap is what turns "no title, ever" into "a title from the
  text", which is what the old behaviour was anyway.
"""
import io
import threading
import time

import pytest
from PIL import Image

from backend.ai import images as images_ai
from backend.ai import jobs as llm_jobs_mod
from backend.routes import journal as journal_routes


@pytest.fixture(autouse=True)
def _isolated_media_root(tmp_path, monkeypatch):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'journal-media'))


@pytest.fixture(autouse=True)
def _no_polish(monkeypatch):
    monkeypatch.setattr(journal_routes, '_polish_bg', lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _fast_wait(monkeypatch):
    """The production cap is 300 s against a model that takes a minute a photo.
    Nothing here calls a model."""
    monkeypatch.setattr(journal_routes, '_METADATA_WAIT_SECONDS', 3.0)
    monkeypatch.setattr(journal_routes, '_METADATA_POLL_SECONDS', 0.01)


@pytest.fixture
def inline_bg(run_jobs_sync):
    """Run queued jobs on the caller's thread. The wait itself is a real
    thread either way — that is the behaviour under test."""


def _jpeg():
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), 'red').save(buf, 'JPEG')
    return buf.getvalue()


def _upload_image(client, entry_id, name='cat.jpg'):
    return client.post(
        f'/api/journal/{entry_id}/attachments',
        data={'file': (io.BytesIO(_jpeg()), name, 'image/jpeg')},
        content_type='multipart/form-data',
    )


def _await_title(client, entry_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        entry = client.get(f'/api/journal/{entry_id}').get_json()
        if entry.get('title'):
            return entry['title']
        time.sleep(0.01)
    return None


@pytest.fixture
def captured_prompt(monkeypatch):
    """What `generate_journal_metadata` was actually handed."""
    seen = {}

    def fake(content, context=None):
        seen['content'] = content
        seen['context'] = context
        return {'title': 'A title', 'tags': ['memory']}

    monkeypatch.setattr(journal_routes, 'generate_journal_metadata', fake)
    return seen


def test_the_title_waits_for_the_caption_and_uses_it(
    client, monkeypatch, inline_bg, captured_prompt
):
    monkeypatch.setattr(
        images_ai, 'get_provider_config', lambda: {'llama_vision_model': 'qwen36'}
    )
    monkeypatch.setattr(
        journal_routes, '_do_attachment_caption',
        lambda _p, _n: 'A grey brick building beside a sign reading ADV METAL.',
    )

    entry_id = client.post(
        '/api/journal', json={'content': 'Look at this.', 'pendingAttachments': 1}
    ).get_json()['id']
    _upload_image(client, entry_id)

    assert _await_title(client, entry_id) == 'A title'
    assert captured_prompt['content'] == 'Look at this.'
    assert 'ADV METAL' in captured_prompt['context']


def test_it_waits_for_every_photo_that_was_promised(
    client, monkeypatch, inline_bg, captured_prompt
):
    """Counting rows is half the check: the composer uploads one file per
    request, so a single arrival must not look like "all of them"."""
    monkeypatch.setattr(
        images_ai, 'get_provider_config', lambda: {'llama_vision_model': 'qwen36'}
    )
    monkeypatch.setattr(
        journal_routes, '_do_attachment_caption', lambda _p, n: f'A photo of {n}.'
    )

    entry_id = client.post(
        '/api/journal', json={'content': 'Two photos.', 'pendingAttachments': 2}
    ).get_json()['id']
    _upload_image(client, entry_id, 'first.jpg')
    _upload_image(client, entry_id, 'second.jpg')

    assert _await_title(client, entry_id) == 'A title'
    assert 'first.jpg' in captured_prompt['context']
    assert 'second.jpg' in captured_prompt['context']


def test_a_title_still_arrives_when_the_upload_never_does(
    client, inline_bg, captured_prompt
):
    """The cap. A create whose uploads failed must not be left untitled forever
    — the entry falls back to exactly the behaviour it had before."""
    entry_id = client.post(
        '/api/journal', json={'content': 'A day.', 'pendingAttachments': 1}
    ).get_json()['id']

    assert _await_title(client, entry_id, timeout=8.0) == 'A title'
    assert captured_prompt['context'] is None


def test_an_entry_with_no_attachments_does_not_wait_at_all(
    client, inline_bg, captured_prompt
):
    """The overwhelmingly common path stays synchronous-ish: no thread, no poll,
    the title is generated on the spot."""
    entry_id = client.post('/api/journal', json={'content': 'A day.'}).get_json()['id']

    assert client.get(f'/api/journal/{entry_id}').get_json()['title'] == 'A title'


def test_the_wait_does_not_occupy_the_shared_background_worker(client, monkeypatch):
    """The deadlock guard, asserted directly.

    The job queue has one worker and the captioning jobs go on it. If the wait
    ran there, it would block them for the full cap and then generate a title
    from captions that could never have been written.
    """
    monkeypatch.setattr(
        images_ai, 'get_provider_config', lambda: {'llama_vision_model': 'qwen36'}
    )
    from backend.ai import jobs as llm_jobs
    queued = []
    monkeypatch.setattr(llm_jobs, 'enqueue',
                        lambda kind, target_id=None, payload=None, **k:
                            queued.append((kind, target_id)))

    client.post(
        '/api/journal', json={'content': 'Waiting.', 'pendingAttachments': 1}
    )

    # Nothing queued yet — the waiter is on its own thread.
    assert queued == []
    assert any(t.name.startswith('journal-meta-') for t in threading.enumerate())


def test_a_nonsense_pending_count_is_clamped(client, inline_bg, captured_prompt):
    """It only ever delays a title, but an unbounded value off the wire would
    park a thread on a condition that cannot come true until the cap expires."""
    entry_id = client.post(
        '/api/journal', json={'content': 'A day.', 'pendingAttachments': 'lots'}
    ).get_json()['id']

    assert client.get(f'/api/journal/{entry_id}').get_json()['title'] == 'A title'


def test_attachments_settled_needs_both_the_count_and_the_status(client, monkeypatch):
    monkeypatch.setattr(
        images_ai, 'get_provider_config', lambda: {'llama_vision_model': 'qwen36'}
    )
    monkeypatch.setattr(llm_jobs_mod, 'enqueue', lambda *a, **k: None)
    entry_id = client.post('/api/journal', json={'content': 'A day.'}).get_json()['id']

    # Nothing uploaded yet: two promised, none present.
    assert journal_routes._attachments_settled(entry_id, 2) is False

    _upload_image(client, entry_id)
    # Present but mid-caption.
    assert journal_routes._attachments_settled(entry_id, 1) is False

    from backend.db.connection import get_db
    db = get_db()
    db.execute(
        "UPDATE journal_attachments SET transcript_status='done',"
        " transcript='A cat.' WHERE entry_id=?",
        (entry_id,),
    )
    db.commit()
    assert journal_routes._attachments_settled(entry_id, 1) is True


def _audio_entry(client, monkeypatch, body, **columns):
    monkeypatch.setattr(llm_jobs_mod, 'enqueue', lambda *a, **k: None)
    entry_id = client.post('/api/journal', json={'content': body}).get_json()['id']
    client.post(
        f'/api/journal/{entry_id}/attachments',
        data={'file': (io.BytesIO(b'\x00' * 32), 'memo.m4a', 'audio/mp4')},
        content_type='multipart/form-data',
    )
    from backend.db.connection import get_db
    db = get_db()
    db.execute(
        'UPDATE journal_attachments SET '
        + ', '.join(f'{c} = ?' for c in columns) + ' WHERE entry_id = ?',
        (*columns.values(), entry_id),
    )
    db.commit()
    return entry_id


def test_a_transcript_already_in_the_body_is_not_fed_back(client, monkeypatch):
    """The dictation path appends a clip's transcript to the entry verbatim.
    Offering it again as context would title the entry from a copy of itself —
    which is what the old blanket exclusion of audio was really about."""
    entry_id = _audio_entry(
        client, monkeypatch, 'Spoken words.',
        transcript_status='done', transcript='Spoken words.',
    )

    assert journal_routes._metadata_context(entry_id) is None


def test_a_transcript_the_body_has_never_seen_is_context(client, monkeypatch):
    """A clip transcribed without being merged, or merged in from another
    entry, carries words the body does not have. Those are exactly what the
    title is missing."""
    entry_id = _audio_entry(
        client, monkeypatch, 'Notes from the walk.',
        transcript_status='done', transcript='I saw a heron by the weir.',
    )

    context = journal_routes._metadata_context(entry_id)
    assert 'heron by the weir' in context
    assert context.startswith('Recording "')


def test_a_recordings_description_always_reaches_the_title(client, monkeypatch):
    """The non-speech description is the one account of a clip the entry's own
    text can never contain, so it is offered whatever the body says."""
    entry_id = _audio_entry(
        client, monkeypatch, 'A walk.',
        description_status='done', description='Heavy rain and a passing train.',
    )

    context = journal_routes._metadata_context(entry_id)
    assert 'Heavy rain' in context


def _record_button_entry(client, monkeypatch=None, **columns):
    """What the bottom bar's Record button makes: an entry with no body at all
    and one clip, through the route that button actually posts to.

    `monkeypatch` stubs the job queue out, for the tests that only want the row.
    A test watching a trigger fire must leave it off — stubbing `enqueue` is
    stubbing the thing under test."""
    if monkeypatch is not None:
        monkeypatch.setattr(llm_jobs_mod, 'enqueue', lambda *a, **k: None)
    res = client.post(
        '/api/journal/recordings',
        data={'file': (io.BytesIO(b'\x00' * 32), 'recording.webm', 'audio/webm')},
        content_type='multipart/form-data',
    ).get_json()
    from backend.db.connection import get_db
    db = get_db()
    if columns:
        db.execute(
            'UPDATE journal_attachments SET '
            + ', '.join(f'{c} = ?' for c in columns) + ' WHERE id = ?',
            (*columns.values(), res['attachment']['id']),
        )
        db.commit()
    return res['id'], res['attachment']['id']


def test_a_bare_recording_is_titled_from_its_description_alone(client, monkeypatch):
    """The entry the whole change is for. Record leaves the body empty and never
    asks for a title, so the description is not merely the best account of the
    entry — it is the only one there is."""
    entry_id, _ = _record_button_entry(
        client, monkeypatch,
        description_status='done', description='Heavy rain and a passing train.',
    )

    assert _entry_body(entry_id) == ''
    context = journal_routes._metadata_context(entry_id)
    assert 'Heavy rain' in context


def _entry_body(entry_id):
    from backend.db.connection import get_db
    row = get_db().execute(
        'SELECT content, raw_content FROM journal_entries WHERE id=?', (entry_id,)
    ).fetchone()
    return (row['raw_content'] or row['content'] or '').strip()


# --- the three late arrivals that retitle ------------------------------------

def _titles(monkeypatch):
    """Every title `_generate_metadata_bg` writes, in order, with the context it
    was given. The jobs run inline (`run_jobs_sync`), so a trigger that fires
    lands here synchronously."""
    seen = []

    def fake(content, context=None):
        seen.append({'content': content, 'context': context})
        return {'title': f'Title {len(seen)}', 'tags': ['memory']}

    monkeypatch.setattr(journal_routes, 'generate_journal_metadata', fake)
    return seen


def test_a_description_landing_retitles_the_entry(client, monkeypatch, inline_bg):
    """The Record button never asks for a title — the route does not call
    `_generate_metadata_bg` at all — so without this the entry stays untitled
    forever, however much the server works out about the clip."""
    seen = _titles(monkeypatch)
    entry_id, attachment_id = _record_button_entry(client)
    assert client.get(f'/api/journal/{entry_id}').get_json().get('title') is None

    monkeypatch.setattr(journal_routes, '_do_attachment_audio_description',
                        lambda _p, _n: 'Heavy rain and a passing train.')
    assert client.post(
        f'/api/journal/attachments/{attachment_id}/describe-audio'
    ).status_code == 202

    assert client.get(f'/api/journal/{entry_id}').get_json()['title'] == 'Title 1'
    assert 'Heavy rain' in seen[0]['context']


def test_a_failed_description_does_not_retitle(client, monkeypatch, inline_bg):
    """Nothing was learned, so there is nothing to retitle from — and a second
    pass would overwrite a good title with one generated from the same inputs."""
    seen = _titles(monkeypatch)
    entry_id, attachment_id = _record_button_entry(client)

    def boom(_p, _n):
        raise RuntimeError('no audio model')

    monkeypatch.setattr(journal_routes, '_do_attachment_audio_description', boom)
    client.post(f'/api/journal/attachments/{attachment_id}/describe-audio')

    assert seen == []


def test_transcribing_without_merging_still_retitles(
    client, monkeypatch, inline_bg
):
    """The attachment's own Transcribe button writes the transcript to the row
    and deliberately not into the body (`into_entry=False`), so the merge branch
    that used to be the only titling trigger never fires for it."""
    seen = _titles(monkeypatch)
    entry_id, attachment_id = _record_button_entry(client)

    monkeypatch.setattr(journal_routes, '_do_attachment_audio',
                        lambda _p: 'I saw a heron by the weir.')
    assert client.post(
        f'/api/journal/attachments/{attachment_id}/transcribe'
    ).status_code == 202

    assert _entry_body(entry_id) == ''  # not merged, by design
    assert client.get(f'/api/journal/{entry_id}').get_json()['title'] == 'Title 1'
    assert 'heron by the weir' in seen[0]['context']


def test_merging_a_recording_retitles_the_target(client, monkeypatch, inline_bg):
    """The third late arrival, and the only one the user triggers by hand: the
    target gains a clip, and with it everything said and heard in it, none of
    which existed when it was titled."""
    seen = _titles(monkeypatch)
    target = client.post(
        '/api/journal', json={'content': 'Notes from the walk.'}
    ).get_json()['id']
    source, attachment_id = _record_button_entry(client)

    from backend.db.connection import get_db
    db = get_db()
    db.execute(
        "UPDATE journal_attachments SET description_status='done',"
        " description='Heavy rain and a passing train.' WHERE id=?",
        (attachment_id,),
    )
    db.commit()
    seen.clear()

    res = client.post(f'/api/journal/{source}/merge', json={'targetId': target})
    assert res.status_code == 200

    assert seen and 'Heavy rain' in seen[-1]['context']
    assert seen[-1]['content'] == 'Notes from the walk.'
    assert client.get(f'/api/journal/{target}').get_json()['title'].startswith('Title ')


# --- a video is the slow case, and gets its own cap --------------------------

def _link_video(entry_id, **columns):
    """A youtube attachment in whatever state the test needs, without the
    import pipeline — this suite is about the wait, not the download."""
    from backend.db.connection import get_db
    from ulid import ULID

    columns = {'import_status': 'importing', 'name': 'A talk', **columns}
    aid = str(ULID())
    db = get_db()
    db.execute(
        "INSERT INTO journal_attachments(id, entry_id, kind, name, path,"
        " position, source_url, import_status, transcript, transcript_status,"
        " description, description_status, created_at)"
        " VALUES (?,?,'youtube',?,'',0,?,?,?,?,?,?,?)",
        (aid, entry_id, columns['name'], 'https://youtu.be/x',
         columns['import_status'], columns.get('transcript'),
         columns.get('transcript_status', 'idle'), columns.get('description'),
         columns.get('description_status', 'idle'), int(time.time())),
    )
    db.commit()
    return aid


def test_the_title_waits_for_a_video_to_finish_downloading(client, monkeypatch):
    """`import_status`, not `transcript_status`: a video that is still being
    fetched has nothing to say about the entry yet."""
    monkeypatch.setattr(llm_jobs_mod, 'enqueue', lambda *a, **k: None)
    entry_id = client.post(
        '/api/journal', json={'content': 'Watched this.'}
    ).get_json()['id']

    aid = _link_video(entry_id)
    assert not journal_routes._attachments_settled(entry_id, 1)

    _set(aid, import_status='ready', transcript='All about neural nets.',
         transcript_status='done', description_status='running')
    assert not journal_routes._attachments_settled(entry_id, 1)

    _set(aid, description='It explains neural nets.', description_status='done')
    assert journal_routes._attachments_settled(entry_id, 1)


def test_an_entry_with_a_video_gets_the_longer_cap(client, monkeypatch):
    """A download plus a transcription plus a summary is work of a different
    order from captioning a photo, so the two caps are different numbers."""
    assert (journal_routes._METADATA_VIDEO_WAIT_SECONDS
            > journal_routes._METADATA_WAIT_SECONDS)

    monkeypatch.setattr(llm_jobs_mod, 'enqueue', lambda *a, **k: None)
    entry_id = client.post('/api/journal', json={'content': 'A day.'}).get_json()['id']
    assert journal_routes._entry_has_video(entry_id) is False
    _link_video(entry_id)
    assert journal_routes._entry_has_video(entry_id) is True


def test_the_video_cap_is_claimed_after_the_link_lands(
    client, inline_bg, captured_prompt, monkeypatch
):
    """The deadline is picked per poll, not when the waiter starts: `attach_link`
    is a separate request that lands after the create, so at start there is no
    video to see. With the short cap in force this entry would be titled before
    its summary; with the long one it waits."""
    monkeypatch.setattr(journal_routes, '_METADATA_WAIT_SECONDS', 0.5)
    monkeypatch.setattr(journal_routes, '_METADATA_VIDEO_WAIT_SECONDS', 8.0)

    entry_id = client.post(
        '/api/journal', json={'content': 'Watched this.', 'pendingAttachments': 1}
    ).get_json()['id']
    aid = _link_video(entry_id)

    # Well past the photo cap, and still no title.
    time.sleep(1.5)
    assert client.get(f'/api/journal/{entry_id}').get_json().get('title') is None

    _set(aid, import_status='ready', transcript='x', transcript_status='done',
         description='It explains neural nets.', description_status='done')

    assert _await_title(client, entry_id) == 'A title'
    assert 'neural nets' in captured_prompt['context']


def test_a_video_that_never_resolves_still_gets_a_title(
    client, inline_bg, captured_prompt, monkeypatch
):
    """The long cap is still a cap. An import that dies mid-download leaves the
    entry titled from its own text, which is the behaviour it had before."""
    monkeypatch.setattr(journal_routes, '_METADATA_VIDEO_WAIT_SECONDS', 1.0)

    entry_id = client.post(
        '/api/journal', json={'content': 'Watched this.', 'pendingAttachments': 1}
    ).get_json()['id']
    _link_video(entry_id)

    assert _await_title(client, entry_id, timeout=8.0) == 'A title'
    assert captured_prompt['context'] is None


def _set(attachment_id, **columns):
    from backend.db.connection import get_db
    db = get_db()
    db.execute(
        'UPDATE journal_attachments SET '
        + ', '.join(f'{c} = ?' for c in columns) + ' WHERE id = ?',
        (*columns.values(), attachment_id),
    )
    db.commit()


def test_an_audio_description_does_not_hold_the_title(client, monkeypatch):
    """Only a *video's* summary is waited on. An audio clip's ambient
    description auto-fires on upload and never reaches the title call, so
    waiting on it would cost every dictated entry a delay for nothing."""
    monkeypatch.setattr(llm_jobs_mod, 'enqueue', lambda *a, **k: None)
    entry_id = client.post('/api/journal', json={'content': 'A day.'}).get_json()['id']

    from backend.db.connection import get_db
    from ulid import ULID
    db = get_db()
    db.execute(
        "INSERT INTO journal_attachments(id, entry_id, kind, name, path, position,"
        " transcript_status, description_status, created_at)"
        " VALUES (?,?,'audio','memo.m4a','',0,'idle','running',?)",
        (str(ULID()), entry_id, int(time.time())),
    )
    db.commit()

    assert journal_routes._attachments_settled(entry_id, 1)


def test_a_waiting_thread_can_be_cancelled_and_drained(client, monkeypatch):
    """The contract `conftest.py` relies on, pinned.

    This waiter is a bare daemon thread — it cannot go on the job queue's single
    worker without deadlocking against the captioning jobs it waits for — so it
    is covered by none of the app's `wait_idle`s. It polls the module-global
    SQLite connection, and a test that closes that connection while the thread
    is mid-query does not raise: it segfaults the interpreter, taking the whole
    pytest batch with it. That happened, which is why this exists.
    """
    monkeypatch.setattr(journal_routes, '_METADATA_WAIT_SECONDS', 60.0)
    monkeypatch.setattr(llm_jobs_mod, 'enqueue', lambda *a, **k: None)

    client.post(
        '/api/journal', json={'content': 'Waiting.', 'pendingAttachments': 3}
    )

    # Parked on a condition that will not come true for a minute.
    assert any(
        t.name.startswith('journal-meta-') for t in threading.enumerate()
    )

    journal_routes.cancel_metadata_waits()
    assert journal_routes.wait_metadata_idle(timeout=5.0) is True
    assert not any(
        t.name.startswith('journal-meta-') for t in threading.enumerate()
    )


def test_a_cancelled_wait_does_not_go_on_to_generate(client, monkeypatch):
    """Cancelling means abandon, not hurry up. Queueing the job anyway would put
    it on the job queue after the suite had already drained it."""
    monkeypatch.setattr(journal_routes, '_METADATA_WAIT_SECONDS', 60.0)
    queued = []
    monkeypatch.setattr(llm_jobs_mod, 'enqueue',
                        lambda kind, target_id=None, payload=None, **k:
                            queued.append((kind, target_id)))

    client.post(
        '/api/journal', json={'content': 'Waiting.', 'pendingAttachments': 3}
    )
    journal_routes.cancel_metadata_waits()
    journal_routes.wait_metadata_idle(timeout=5.0)

    assert queued == []


def test_draining_is_a_no_op_when_nothing_is_waiting(client):
    assert journal_routes.wait_metadata_idle(timeout=1.0) is True
    journal_routes.cancel_metadata_waits()


def test_a_finished_waiter_removes_itself_from_the_registry(client, monkeypatch):
    """Otherwise the registry grows for the life of the process, holding a
    reference to every thread that ever waited — and `wait_metadata_idle` walks
    it on every call."""
    monkeypatch.setattr(llm_jobs_mod, 'enqueue', lambda *a, **k: None)

    client.post('/api/journal', json={'content': 'A day.', 'pendingAttachments': 1})
    journal_routes.cancel_metadata_waits()
    journal_routes.wait_metadata_idle(timeout=5.0)

    assert journal_routes._metadata_waiters == {}
