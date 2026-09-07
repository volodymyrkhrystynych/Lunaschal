"""A meal that was spoken over.

`POST /api/food/recordings` is the food log's half of the durable recording
path, and it mirrors `POST /api/journal/recordings` because the contract is the
same one: the phone holds the audio until the server confirms it and re-POSTs on
every reconnect, so both ids come from the client, a replay is a no-op, and a
clip that outruns the meal it belongs to still lands.

What is pinned here is that, plus what the transcript then does — appended to
the meal's raw note in the order the clips were spoken, once per clip, with the
structuring pass reading the whole thing.
"""
import io

import pytest
from ulid import ULID

from backend.routes import food as food_routes


@pytest.fixture(autouse=True)
def _isolated_media_root(tmp_path, monkeypatch):
    monkeypatch.setenv('FOOD_ROOT', str(tmp_path / 'food-media'))
    from backend.food import storage

    monkeypatch.setattr(storage, '_root_override', None, raising=False)
    yield


def _run_pending_bg(monkeypatch):
    """Hold queued jobs back so a test can run them and assert on what they
    wrote."""
    from backend.ai import job_handlers  # noqa: F401  (registers handlers)
    from backend.ai import jobs as llm_jobs

    captured = []
    real = llm_jobs.enqueue

    def capture(kind, target_id=None, payload=None, *, commit=True):
        job_id = real(kind, target_id, payload, commit=commit)
        if job_id is not None:
            captured.append((kind, lambda jid=job_id: llm_jobs.process_one(jid)))
        return job_id

    monkeypatch.setattr(llm_jobs, 'enqueue', capture)
    return captured


def _run_kind(jobs, kind):
    ran = 0
    for job_kind, run in list(jobs):
        if job_kind == kind:
            run()
            ran += 1
    return ran


def _clip(client, entry_id, media_id, *, position=None, data=b'\x00' * 2048,
          filename='recording.webm', mime='audio/webm'):
    form = {
        'audio': (io.BytesIO(data), filename, mime),
        'id': entry_id,
        'mediaId': media_id,
    }
    if position is not None:
        form['position'] = str(position)
    return client.post(
        '/api/food/recordings', data=form, content_type='multipart/form-data'
    )


def _transcribes(monkeypatch, text='Pad thai from the place on Bloor.'):
    from backend.routes import stt as stt_routes

    monkeypatch.setattr(stt_routes, 'transcribe_file', lambda _p, **k: text)


def _structures(monkeypatch):
    """Record what the structuring pass was handed, without a model call."""
    seen = []
    monkeypatch.setattr(
        food_routes, 'structure_food_entry', lambda _id, text: seen.append(text)
    )
    return seen


# --- the upload ---------------------------------------------------------------

def test_a_clip_creates_the_meal_it_belongs_to(client, monkeypatch):
    """A clip can outrun its meal — the composer sends the create first, but the
    boot sweep after a crash sends only the clip. The alternative to creating
    the row here is a 404 that strands the audio."""
    _run_pending_bg(monkeypatch)
    entry_id, media_id = str(ULID()), str(ULID())

    r = _clip(client, entry_id, media_id)
    assert r.status_code == 201
    body = r.get_json()
    assert body['id'] == entry_id
    assert body['media']['kind'] == 'audio'
    assert body['media']['transcriptStatus'] == 'running'

    entry = client.get(f'/api/food/{entry_id}').get_json()
    assert [m['kind'] for m in entry['media']] == ['audio']


def test_the_clip_is_audio_even_though_webm_could_be_video(client, monkeypatch):
    """`recording.webm` says nothing about which it is — the container carries
    either, and an ambiguous one is read as video everywhere else. This route
    knows it is holding a voice memo and says so."""
    _run_pending_bg(monkeypatch)
    entry_id, media_id = str(ULID()), str(ULID())
    _clip(client, entry_id, media_id, filename='recording.webm', mime='')

    entry = client.get(f'/api/food/{entry_id}').get_json()
    assert entry['media'][0]['kind'] == 'audio'


def test_a_replayed_upload_is_a_no_op(client, monkeypatch):
    _run_pending_bg(monkeypatch)
    entry_id, media_id = str(ULID()), str(ULID())

    first = _clip(client, entry_id, media_id)
    second = _clip(client, entry_id, media_id)
    assert (first.status_code, second.status_code) == (201, 201)

    entry = client.get(f'/api/food/{entry_id}').get_json()
    # One row, one file — not a second copy of the recording.
    assert len(entry['media']) == 1


def test_a_replay_does_not_re_queue_the_transcription(client, monkeypatch):
    jobs = _run_pending_bg(monkeypatch)
    entry_id, media_id = str(ULID()), str(ULID())
    _clip(client, entry_id, media_id)
    _clip(client, entry_id, media_id)

    queued = [k for k, _ in jobs if k == 'food.transcribe_media']
    assert len(queued) == 1


def test_an_unsupported_upload_leaves_no_empty_meal_behind(client, monkeypatch):
    _run_pending_bg(monkeypatch)
    entry_id, media_id = str(ULID()), str(ULID())

    r = _clip(client, entry_id, media_id, filename='notes.txt', mime='text/plain')
    assert r.status_code == 400
    # The meal this request would otherwise have invented is gone again.
    assert client.get(f'/api/food/{entry_id}').status_code == 404


def test_a_rejected_replay_does_not_delete_a_real_meal(client, monkeypatch):
    """The rollback above must only reach a meal that is still empty."""
    _run_pending_bg(monkeypatch)
    created = client.post('/api/food', json={'text': 'Ramen'}).get_json()
    entry_id = created['id']

    r = _clip(client, entry_id, str(ULID()), filename='x.txt', mime='text/plain')
    assert r.status_code == 400
    assert client.get(f'/api/food/{entry_id}').status_code == 200


# --- the transcript -----------------------------------------------------------

def test_the_transcript_lands_on_the_clip_and_the_meal(client, monkeypatch):
    jobs = _run_pending_bg(monkeypatch)
    _transcribes(monkeypatch)
    _structures(monkeypatch)
    entry_id, media_id = str(ULID()), str(ULID())
    _clip(client, entry_id, media_id)

    _run_kind(jobs, 'food.transcribe_media')

    entry = client.get(f'/api/food/{entry_id}').get_json()
    assert entry['rawContent'] == 'Pad thai from the place on Bloor.'
    clip = entry['media'][0]
    assert clip['transcript'] == 'Pad thai from the place on Bloor.'
    assert clip['transcriptStatus'] == 'done'


def test_two_clips_append_in_order_with_a_blank_line_between_them(
    client, monkeypatch
):
    jobs = _run_pending_bg(monkeypatch)
    _structures(monkeypatch)
    entry_id, m1, m2 = str(ULID()), str(ULID()), str(ULID())
    _clip(client, entry_id, m1, position=0)
    _clip(client, entry_id, m2, position=1)

    from backend.db.connection import get_db
    from backend.food import storage
    from backend.routes import stt as stt_routes

    said = {}
    for media_id, text in ((m1, 'Ordered the pad thai.'), (m2, 'Still the best.')):
        row = get_db().execute(
            'SELECT path FROM food_media WHERE id=?', (media_id,)
        ).fetchone()
        said[str(storage.resolve_stored_path(row['path']))] = text
    monkeypatch.setattr(
        stt_routes, 'transcribe_file', lambda p, **k: said[str(p)]
    )

    _run_kind(jobs, 'food.transcribe_media')

    entry = client.get(f'/api/food/{entry_id}').get_json()
    # The gap is the pause that was taken between them.
    assert entry['rawContent'] == 'Ordered the pad thai.\n\nStill the best.'
    assert [m['position'] for m in entry['media']] == [0, 1]


def test_the_transcript_appends_after_what_was_typed(client, monkeypatch):
    jobs = _run_pending_bg(monkeypatch)
    _transcribes(monkeypatch, text='and the sauce was too sweet')
    _structures(monkeypatch)
    created = client.post('/api/food', json={'text': 'Pad thai'}).get_json()

    _clip(client, created['id'], str(ULID()))
    _run_kind(jobs, 'food.transcribe_media')

    entry = client.get(f"/api/food/{created['id']}").get_json()
    assert entry['rawContent'] == 'Pad thai\n\nand the sauce was too sweet'


def test_structuring_reads_the_whole_note_including_the_clips(
    client, monkeypatch
):
    jobs = _run_pending_bg(monkeypatch)
    _transcribes(monkeypatch, text='Pad thai, four stars.')
    structured = _structures(monkeypatch)
    entry_id = str(ULID())
    _clip(client, entry_id, str(ULID()))

    _run_kind(jobs, 'food.transcribe_media')
    _run_kind(jobs, 'food.structure')

    # A meal that was only spoken still gets structured — before this the pass
    # only ran when the create carried text, so a spoken meal stayed unparsed.
    assert structured == ['Pad thai, four stars.']


def test_a_failed_transcription_says_so_and_keeps_the_audio(
    client, monkeypatch
):
    jobs = _run_pending_bg(monkeypatch)
    _structures(monkeypatch)
    from backend.routes import stt as stt_routes

    def _boom(_p, **k):
        raise RuntimeError('No speech found in the recording')

    monkeypatch.setattr(stt_routes, 'transcribe_file', _boom)
    entry_id, media_id = str(ULID()), str(ULID())
    _clip(client, entry_id, media_id)

    _run_kind(jobs, 'food.transcribe_media')

    entry = client.get(f'/api/food/{entry_id}').get_json()
    clip = entry['media'][0]
    assert clip['transcriptStatus'] == 'error'
    assert clip['transcriptError'] == 'No speech found in the recording'
    # No text is better than a wrong one, and the recording is still playable.
    assert entry['rawContent'] is None
    assert client.get(f'/api/food/media/{media_id}').status_code == 200


def test_a_photo_carries_no_transcript_fields(client, monkeypatch):
    """A meal photographed and not spoken over must not render an empty
    Transcript block under the picture."""
    _run_pending_bg(monkeypatch)
    created = client.post(
        '/api/food',
        data={
            'text': 'Ramen',
            'media': (io.BytesIO(b'\xff\xd8\xff' + b'\x00' * 64), 'a.jpg', 'image/jpeg'),
        },
        content_type='multipart/form-data',
    ).get_json()

    photo = created['media'][0]
    assert photo['kind'] == 'image'
    assert 'transcript' not in photo


def test_a_spoken_only_meal_can_be_created_at_all(client):
    """The create carries no text and no photo — `pendingClips` is what says it
    is a meal rather than an empty request, and it is what keeps the GPS."""
    r = client.post(
        '/api/food',
        data={'pendingClips': '1', 'latitude': '43.6532', 'longitude': '-79.3832'},
        content_type='multipart/form-data',
    )
    assert r.status_code == 201
    assert r.get_json()['latitude'] == pytest.approx(43.6532)


def test_an_empty_create_is_still_refused(client):
    assert client.post('/api/food', json={}).status_code == 400


def test_a_clip_that_lands_before_the_create_does_not_swallow_the_typed_note(
    client, monkeypatch
):
    """The meal's id is minted at the first chunk, so the two halves can arrive
    in either order. The create used to be a plain INSERT OR IGNORE, which meant
    a clip getting there first silently dropped what was typed with it."""
    jobs = _run_pending_bg(monkeypatch)
    _transcribes(monkeypatch, text='and it was too salty')
    _structures(monkeypatch)
    entry_id = str(ULID())

    _clip(client, entry_id, str(ULID()))
    assert client.get(f'/api/food/{entry_id}').get_json()['rawContent'] is None

    client.post('/api/food', json={'id': entry_id, 'text': 'Ramen'})
    _run_kind(jobs, 'food.transcribe_media')

    entry = client.get(f'/api/food/{entry_id}').get_json()
    assert entry['rawContent'] == 'Ramen\n\nand it was too salty'


def test_a_replayed_create_still_leaves_a_real_meal_alone(client):
    entry_id = str(ULID())
    client.post('/api/food', json={'id': entry_id, 'text': 'The real one'})
    client.post('/api/food', json={'id': entry_id, 'text': 'A replay of it'})

    entry = client.get(f'/api/food/{entry_id}').get_json()
    assert entry['rawContent'] == 'The real one'
