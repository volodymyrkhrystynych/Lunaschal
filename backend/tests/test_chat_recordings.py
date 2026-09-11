"""A chat message that was spoken.

`POST /api/chat/conversations/<id>/recordings` is the Chat tab's half of the
durable recording path, and it mirrors `POST /api/food/recordings` because the
contract is the same one: the phone holds the audio until the server confirms
it and re-POSTs on every reconnect, so both ids come from the client, a replay
is a no-op, and a clip that outruns the conversation still lands.

What is pinned here on top of that is the half only chat has — the transcript is
a *question*, so the server answers it without anybody holding a connection. The
point of the feature is that stopping the recording is the last thing the user
has to be present for, and every test below is some way that could stop being
true.
"""
import io

import pytest
from ulid import ULID

from backend.chat import autoreply
from backend.db.connection import get_db
from backend.routes import chat as chat_routes


@pytest.fixture(autouse=True)
def chat_root(monkeypatch, tmp_path):
    root = tmp_path / 'chat'
    monkeypatch.setenv('CHAT_ROOT', str(root))
    return root


@pytest.fixture(autouse=True)
def ai_configured(monkeypatch):
    """The auto-reply declines when AI is unconfigured, which is most of what a
    test box looks like. Tests that want the decline override this."""
    monkeypatch.setattr(autoreply, 'is_ai_configured', lambda: True)


@pytest.fixture
def started_runs(monkeypatch):
    """Capture `runs.start` instead of spawning a real generation thread."""
    started = []

    def fake_start(message_id, messages, system_prompt, *, tools_enabled,
                   conversation_id=None):
        started.append({
            'messageId': message_id,
            'messages': messages,
            'systemPrompt': system_prompt,
            'toolsEnabled': tools_enabled,
            'conversationId': conversation_id,
        })

    monkeypatch.setattr(autoreply.runs, 'start', fake_start)
    return started


@pytest.fixture
def transcribes(monkeypatch):
    """Stand in for the STT pipeline. Returns a holder the test can retune."""
    holder = {'text': 'What is the capital of Peru?', 'error': None, 'calls': []}

    def fake(path, **kwargs):
        holder['calls'].append(str(path))
        if holder['error']:
            raise RuntimeError(holder['error'])
        return holder['text']

    import backend.routes.stt as stt_routes
    monkeypatch.setattr(stt_routes, 'transcribe_file', fake)
    return holder


@pytest.fixture(autouse=True)
def sync_bg(run_jobs_sync):
    """Run the transcription job inline so upload -> transcript -> reply is
    deterministic."""


def _conversation(client) -> str:
    return client.post('/api/chat/conversations', json={}).get_json()['id']


def _record(client, conv_id, *, message_id=None, attachment_id=None,
            data=b'\x00' * 2048, filename='recording.webm', mime='audio/webm',
            text=None, attachment_ids=None):
    form = {
        'audio': (io.BytesIO(data), filename, mime),
        'messageId': message_id or str(ULID()),
        'attachmentId': attachment_id or str(ULID()),
    }
    if text is not None:
        form['text'] = text
    if attachment_ids is not None:
        import json
        form['attachmentIds'] = json.dumps(attachment_ids)
    res = client.post(f'/api/chat/conversations/{conv_id}/recordings',
                      data=form, content_type='multipart/form-data')
    return res, form['messageId'], form['attachmentId']


# --- the audio lands first ---------------------------------------------------

def test_the_clip_is_stored_and_becomes_a_message(client, transcribes, started_runs,
                                                  chat_root):
    conv = _conversation(client)
    res, message_id, attachment_id = _record(client, conv)
    assert res.status_code == 201

    body = res.get_json()
    assert body['id'] == message_id
    assert body['attachment']['kind'] == 'audio'

    db = get_db()
    message = db.execute('SELECT * FROM messages WHERE id=?', (message_id,)).fetchone()
    assert message['role'] == 'user'
    assert message['conversation_id'] == conv

    row = db.execute('SELECT * FROM chat_attachments WHERE id=?',
                     (attachment_id,)).fetchone()
    assert row['message_id'] == message_id
    # The audio is on disk under the conversation, which is what makes deleting
    # a conversation one directory removal.
    stored = list((chat_root / conv).glob(f'{attachment_id}.*'))
    assert len(stored) == 1 and stored[0].read_bytes() == b'\x00' * 2048


def test_a_clip_can_outrun_its_conversation(client, transcribes, started_runs):
    """The composer creates the conversation first, but a boot sweep after a
    crash sends only the clip. A 404 there would strand the audio."""
    conv = str(ULID())
    res, message_id, _ = _record(client, conv)
    assert res.status_code == 201

    db = get_db()
    assert db.execute('SELECT 1 FROM conversations WHERE id=?', (conv,)).fetchone()
    assert db.execute(
        'SELECT conversation_id FROM messages WHERE id=?', (message_id,)
    ).fetchone()['conversation_id'] == conv


def test_a_replay_is_a_no_op_and_does_not_rewrite_the_file(client, transcribes,
                                                           started_runs, chat_root):
    conv = _conversation(client)
    res, message_id, attachment_id = _record(client, conv, data=b'\x01' * 2048)
    assert res.status_code == 201

    # The same ids with different bytes: the guard has to fire before the file is
    # read, so the stored recording must be untouched.
    again, _, _ = _record(client, conv, message_id=message_id,
                          attachment_id=attachment_id, data=b'\x02' * 4096)
    assert again.status_code == 201
    assert again.get_json()['id'] == message_id

    stored = list((chat_root / conv).glob(f'{attachment_id}.*'))
    assert len(stored) == 1 and stored[0].read_bytes() == b'\x01' * 2048

    db = get_db()
    assert db.execute('SELECT COUNT(*) c FROM chat_attachments').fetchone()['c'] == 1
    assert db.execute(
        "SELECT COUNT(*) c FROM messages WHERE role='user'"
    ).fetchone()['c'] == 1


def test_an_unsupported_file_is_refused_and_leaves_no_message(client, started_runs):
    conv = _conversation(client)
    res, message_id, _ = _record(client, conv, filename='notes.txt', mime='text/plain')
    assert res.status_code == 400

    db = get_db()
    assert db.execute('SELECT 1 FROM messages WHERE id=?', (message_id,)).fetchone() is None


def test_an_empty_recording_rolls_back_the_message_it_created(client, started_runs):
    conv = _conversation(client)
    res, message_id, _ = _record(client, conv, data=b'')
    assert res.status_code == 400

    db = get_db()
    assert db.execute('SELECT 1 FROM messages WHERE id=?', (message_id,)).fetchone() is None
    assert db.execute('SELECT COUNT(*) c FROM chat_attachments').fetchone()['c'] == 0


def test_a_photo_route_upload_refuses_audio(client):
    """`resolve_ext` answers for audio now; the photo door must not."""
    res = client.post(
        f'/api/chat/conversations/{_conversation(client)}/attachments',
        data={'image': (io.BytesIO(b'\x00' * 64), 'clip.m4a', 'audio/mp4')},
        content_type='multipart/form-data',
    )
    assert res.status_code == 400


# --- then the words ----------------------------------------------------------

def test_the_transcript_becomes_the_message(client, transcribes, started_runs):
    conv = _conversation(client)
    transcribes['text'] = 'What is the capital of Peru?'
    _, message_id, attachment_id = _record(client, conv)

    db = get_db()
    assert db.execute(
        'SELECT content FROM messages WHERE id=?', (message_id,)
    ).fetchone()['content'] == 'What is the capital of Peru?'
    row = db.execute('SELECT transcript, transcript_status FROM chat_attachments'
                     ' WHERE id=?', (attachment_id,)).fetchone()
    assert row['transcript_status'] == 'done'
    assert row['transcript'] == 'What is the capital of Peru?'


def test_typed_text_keeps_its_place_in_front_of_the_transcript(client, transcribes,
                                                               started_runs):
    """Half a message typed and the rest spoken is one message, in the order it
    happened."""
    conv = _conversation(client)
    transcribes['text'] = 'and how far is it from Lima?'
    _, message_id, _ = _record(client, conv, text='About Cusco:')

    assert get_db().execute(
        'SELECT content FROM messages WHERE id=?', (message_id,)
    ).fetchone()['content'] == 'About Cusco:\n\nand how far is it from Lima?'


def test_a_re_run_refreshes_the_clip_without_duplicating_the_message(
        client, transcribes, started_runs):
    conv = _conversation(client)
    _, message_id, attachment_id = _record(client, conv)
    before = get_db().execute(
        'SELECT content FROM messages WHERE id=?', (message_id,)
    ).fetchone()['content']

    transcribes['text'] = 'What is the capital of Peru, really?'
    path = get_db().execute('SELECT path FROM chat_attachments WHERE id=?',
                            (attachment_id,)).fetchone()['path']
    chat_routes._transcribe_recording_bg(attachment_id, message_id, path, now=True)

    db = get_db()
    assert db.execute(
        'SELECT content FROM messages WHERE id=?', (message_id,)
    ).fetchone()['content'] == before
    # The clip's own text does refresh — it is the message body that must not
    # grow a second copy.
    assert db.execute(
        'SELECT transcript FROM chat_attachments WHERE id=?', (attachment_id,)
    ).fetchone()['transcript'] == 'What is the capital of Peru, really?'


def test_a_failed_transcription_is_recorded_and_answers_nothing(client, transcribes,
                                                                started_runs):
    conv = _conversation(client)
    transcribes['error'] = 'No speech found in the recording'
    _, message_id, attachment_id = _record(client, conv)

    db = get_db()
    row = db.execute('SELECT transcript, transcript_status, transcript_error'
                     ' FROM chat_attachments WHERE id=?', (attachment_id,)).fetchone()
    assert row['transcript_status'] == 'error'
    assert row['transcript'] is None
    assert 'No speech' in row['transcript_error']
    # The message and its audio survive the failure — that is the whole trade
    # the durable path exists to make.
    assert db.execute('SELECT 1 FROM messages WHERE id=?', (message_id,)).fetchone()
    assert started_runs == []


# --- and then it answers -----------------------------------------------------

def test_the_reply_starts_on_its_own(client, transcribes, started_runs):
    """Nobody is holding a connection: the run is started by the job that
    finished the transcript."""
    conv = _conversation(client)
    _, message_id, _ = _record(client, conv)

    assert len(started_runs) == 1
    run = started_runs[0]
    assert run['conversationId'] == conv
    assert run['toolsEnabled'] is True
    # Empty, because `stream_reply` builds the real system prompt itself — a
    # caller-supplied one would turn the whole toolbox off.
    assert run['systemPrompt'] == ''

    # The assistant row exists and is 'streaming', which is what the Chat tab's
    # poll watches for.
    row = get_db().execute('SELECT role, status FROM messages WHERE id=?',
                           (run['messageId'],)).fetchone()
    assert (row['role'], row['status']) == ('assistant', 'streaming')


def test_the_run_is_handed_the_question_it_is_answering(client, transcribes,
                                                        started_runs):
    conv = _conversation(client)
    client.post(f'/api/chat/conversations/{conv}/messages',
                json={'role': 'user', 'content': 'Hello'})
    client.post(f'/api/chat/conversations/{conv}/messages',
                json={'role': 'assistant', 'content': 'Hi.'})
    transcribes['text'] = 'What is the capital of Peru?'
    _record(client, conv)

    messages = started_runs[0]['messages']
    assert [m['content'] for m in messages] == [
        'Hello', 'Hi.', 'What is the capital of Peru?',
    ]
    # `createdAt` has to be the ISO string `stamp_messages` parses, or every turn
    # reaches the model unstamped and it goes blind to the gaps in the day.
    assert all(isinstance(m['createdAt'], str) and 'T' in m['createdAt']
               for m in messages)


def test_only_the_current_segment_is_sent(client, transcribes, started_runs):
    """A "New chat" break is a boundary here exactly as it is in the browser."""
    conv = _conversation(client)
    client.post(f'/api/chat/conversations/{conv}/messages',
                json={'role': 'user', 'content': 'Before the break'})
    client.post(f'/api/chat/conversations/{conv}/break', json={'carryContext': False})
    transcribes['text'] = 'After the break'
    _record(client, conv)

    assert [m['content'] for m in started_runs[0]['messages']] == ['After the break']


def test_a_replayed_clip_does_not_answer_twice(client, transcribes, started_runs):
    conv = _conversation(client)
    _, message_id, attachment_id = _record(client, conv)
    assert len(started_runs) == 1

    # Finish the first reply so the 'streaming' guard is not the thing catching
    # this — it is the already-answered check that has to.
    get_db().execute("UPDATE messages SET status='done' WHERE id=?",
                     (started_runs[0]['messageId'],))
    get_db().commit()

    path = get_db().execute('SELECT path FROM chat_attachments WHERE id=?',
                            (attachment_id,)).fetchone()['path']
    chat_routes._transcribe_recording_bg(attachment_id, message_id, path, now=True)
    assert len(started_runs) == 1


def test_two_clips_landing_together_start_one_reply(client, transcribes, started_runs):
    """The second clip's job finds a reply already being written."""
    conv = _conversation(client)
    _record(client, conv)
    _record(client, conv)
    assert len(started_runs) == 1


def test_no_reply_when_ai_is_unconfigured(client, transcribes, started_runs,
                                          monkeypatch):
    monkeypatch.setattr(autoreply, 'is_ai_configured', lambda: False)
    conv = _conversation(client)
    _, message_id, _ = _record(client, conv)

    assert started_runs == []
    # The transcript still landed: STT is CPU work and does not need the model.
    assert get_db().execute(
        'SELECT content FROM messages WHERE id=?', (message_id,)
    ).fetchone()['content'] == 'What is the capital of Peru?'


def test_an_empty_transcript_is_never_asked_as_a_question(client, transcribes,
                                                          started_runs):
    """A silent clip would otherwise become a turn consisting of a timestamp."""
    conv = _conversation(client)
    transcribes['text'] = '   '
    _record(client, conv)
    assert started_runs == []


# --- staged photos -----------------------------------------------------------

def test_photos_staged_while_talking_ride_on_the_message(client, transcribes,
                                                         started_runs):
    from PIL import Image

    conv = _conversation(client)
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), (10, 10, 10)).save(buf, 'JPEG')
    photo = client.post(f'/api/chat/conversations/{conv}/attachments',
                        data={'image': (io.BytesIO(buf.getvalue()), 'plate.jpg')},
                        content_type='multipart/form-data').get_json()[0]

    _, message_id, attachment_id = _record(client, conv, attachment_ids=[photo['id']])

    rows = get_db().execute(
        'SELECT id, kind FROM chat_attachments WHERE message_id=? ORDER BY position',
        (message_id,),
    ).fetchall()
    # The picture was attached first and the clip is what was said about it.
    assert [(r['id'], r['kind']) for r in rows] == [
        (photo['id'], 'image'), (attachment_id, 'audio'),
    ]


def test_a_photo_and_a_spoken_question_arrive_as_one_turn(client, transcribes,
                                                          started_runs, monkeypatch):
    """Attach a picture, then speak about it: one message, both attachments, and
    the model gets the reading *and* the words.

    The photo is read by a job queued when it was attached; the clip is
    transcribed by a job queued when the recording landed. The worker is a
    single FIFO thread, so the reading is finished before the transcript exists
    — which is what stops the turn going out saying the photo could not be read.
    """
    from PIL import Image
    from backend.chat import context as chat_context

    monkeypatch.setattr(chat_routes, '_do_read_attachment',
                        lambda path: 'A plate of vareniki.')

    conv = _conversation(client)
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), (10, 10, 10)).save(buf, 'JPEG')
    photo = client.post(f'/api/chat/conversations/{conv}/attachments',
                        data={'image': (io.BytesIO(buf.getvalue()), 'plate.jpg')},
                        content_type='multipart/form-data').get_json()[0]

    transcribes['text'] = 'How many calories is this?'
    _, message_id, clip_id = _record(client, conv, attachment_ids=[photo['id']])

    # One message, carrying both.
    assert len(started_runs) == 1
    messages = started_runs[0]['messages']
    assert len(messages) == 1
    assert messages[0]['content'] == 'How many calories is this?'
    assert messages[0]['attachmentIds'] == [photo['id'], clip_id]

    # And what the model is actually handed: the spoken words, plus the reading
    # of the picture — with nothing about the clip, whose words are the content.
    expanded = chat_context.expand_attachments(messages)
    content = expanded[0]['content']
    assert 'How many calories is this?' in content
    assert 'A plate of vareniki.' in content
    assert 'could not be read' not in content
    assert 'has not finished being read' not in content


def test_a_clip_is_never_handed_to_the_vision_path(client, transcribes, started_runs):
    """Its words are in the message body already; the file is not a picture."""
    from backend.chat import context as chat_context

    conv = _conversation(client)
    _, message_id, attachment_id = _record(client, conv)

    assert chat_context.descriptions_for([attachment_id]) == []
    assert chat_context.image_parts_for([attachment_id]) == ([], [])
    # And the message passes through `expand_attachments` untouched rather than
    # picking up an "[Photo attached, but it could not be read]" note.
    expanded = chat_context.expand_attachments(
        [{'role': 'user', 'content': 'spoken', 'attachmentIds': [attachment_id]}]
    )
    assert expanded[0]['content'] == 'spoken'
