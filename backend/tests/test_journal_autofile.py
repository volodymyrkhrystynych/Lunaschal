"""Auto-filing: a phone capture saved with the Capture tab's Save button that
turns out to be a meal or a purchase is moved out of the journal.

What has to hold:

- Only a capture the phone flagged is ever read, and only once its photos and
  clips have settled. Out of time is not settled.
- Doubt of any kind — an unsure model, an unreachable one, an entry something
  else already points at — leaves it in the journal, titled as before.
- A moved entry keeps its id and its media ids, and the phone's replays of the
  create, a file or a clip are answered with the 201 it validates rather than
  failing the capture or bringing the journal entry back.
"""
import io
from pathlib import Path

import pytest
from PIL import Image
from ulid import ULID

from backend.ai import autofile, jobs, service
from backend.ai import images as images_ai
from backend.db.connection import get_db
from backend.routes import food as food_routes
from backend.routes import journal as journal_routes
from backend.routes import spending as spending_routes


@pytest.fixture(autouse=True)
def _media_roots(tmp_path, monkeypatch):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'journal'))
    monkeypatch.setenv('FOOD_ROOT', str(tmp_path / 'food'))
    monkeypatch.setenv('SPENDING_ROOT', str(tmp_path / 'spending'))


@pytest.fixture(autouse=True)
def _no_models(monkeypatch):
    """Every model the move can reach, answered without one."""
    monkeypatch.setattr(journal_routes, '_polish_bg', lambda *a, **k: None)
    monkeypatch.setattr(journal_routes, 'generate_journal_metadata',
                        lambda content, context=None: {'title': 'A title', 'tags': ['memory']})
    monkeypatch.setattr(food_routes, 'parse_food_entry', lambda *a, **k: None)
    monkeypatch.setattr(spending_routes, 'parse_spending_entry', lambda *a, **k: None)
    monkeypatch.setattr(journal_routes, '_METADATA_WAIT_SECONDS', 3.0)
    monkeypatch.setattr(journal_routes, '_METADATA_POLL_SECONDS', 0.01)


@pytest.fixture
def verdict(monkeypatch):
    """What the classifier will say, and what it was shown."""
    state = {'answer': {'kind': 'journal', 'confident': True}, 'prompts': []}

    def fake(prompt, system=None, schema=None, **_):
        state['prompts'].append(prompt)
        answer = state['answer']
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(autofile, 'chat_json', fake)
    monkeypatch.setattr(autofile, 'is_ai_configured', lambda: True)
    return state


@pytest.fixture
def vision(monkeypatch):
    monkeypatch.setattr(images_ai, 'get_provider_config',
                        lambda: {'llama_vision_model': 'gemma4-12b-omni'})
    monkeypatch.setattr(journal_routes, '_do_attachment_caption',
                        lambda _p, _n: 'A bowl of pho with basil and lime.')
    monkeypatch.setattr(images_ai, 'describe_image', lambda *a, **k: 'Read again by the log.')


def _jpeg():
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), 'red').save(buf, 'JPEG')
    return buf.getvalue()


def _create(client, content='', *, pending=0, auto=True, entry_id=None):
    body = {'id': entry_id or str(ULID()), 'content': content,
            'pendingAttachments': str(pending)}
    if auto:
        body['autoFile'] = '1'
    res = client.post('/api/journal', json=body)
    assert res.status_code == 201, res.get_json()
    return body['id']


def _upload(client, entry_id, attachment_id, name='plate.jpg'):
    return client.post(
        f'/api/journal/{entry_id}/attachments',
        data={'file': (io.BytesIO(_jpeg()), name, 'image/jpeg'), 'attachmentId': attachment_id},
        content_type='multipart/form-data',
    )


def _clip(client, entry_id, attachment_id):
    return client.post(
        '/api/journal/recordings',
        data={'file': (io.BytesIO(b'RIFF' + b'\0' * 64), 'clip.m4a', 'audio/mp4'),
              'id': entry_id, 'attachmentId': attachment_id},
        content_type='multipart/form-data',
    )


def _settle():
    assert journal_routes.wait_metadata_idle(timeout=10.0)


def _row(sql, *args):
    return get_db().execute(sql, args).fetchone()


# --- the classifier ----------------------------------------------------------

def test_a_confident_meal_is_food(verdict):
    verdict['answer'] = {'kind': 'food', 'confident': True}
    assert autofile.classify_capture('had pho for lunch', []) == 'food'


def test_doubt_is_journal(verdict):
    verdict['answer'] = {'kind': 'food', 'confident': False}
    assert autofile.classify_capture('had pho for lunch', []) == 'journal'


def test_a_failed_call_is_journal(verdict):
    verdict['answer'] = RuntimeError('llama-server is down')
    assert autofile.classify_capture('had pho for lunch', []) == 'journal'


def test_a_malformed_answer_is_journal(verdict):
    verdict['answer'] = {'kind': 'recipe', 'confident': True}
    assert autofile.classify_capture('had pho for lunch', []) == 'journal'


def test_a_video_is_never_a_purchase(verdict):
    verdict['answer'] = {'kind': 'spending', 'confident': True}
    assert autofile.classify_capture('the till', [], has_video=True) == 'journal'


def test_a_paused_model_is_not_an_answer(verdict):
    verdict['answer'] = service.InferencePaused('paused')
    with pytest.raises(service.InferencePaused):
        autofile.classify_capture('had pho for lunch', [])


def test_the_attachments_are_what_it_reads(verdict):
    autofile.classify_capture('', ['Photo — plate.jpg: a bowl of pho'])
    assert 'a bowl of pho' in verdict['prompts'][0]


def test_nothing_to_read_asks_nothing(verdict):
    assert autofile.classify_capture('  ', []) == 'journal'
    assert verdict['prompts'] == []


# --- when it runs ------------------------------------------------------------

def test_an_unflagged_entry_is_only_titled(client, queued_jobs):
    _create(client, 'had pho for lunch', auto=False)
    assert [j['kind'] for j in queued_jobs] == ['journal.metadata']


def test_a_flagged_entry_is_sorted_before_it_is_titled(client, queued_jobs):
    _create(client, 'had pho for lunch')
    assert [j['kind'] for j in queued_jobs] == ['journal.autofile']


def test_a_capture_whose_photo_never_arrives_stays_in_the_journal(client, queued_jobs, monkeypatch):
    monkeypatch.setattr(journal_routes, '_METADATA_WAIT_SECONDS', 0.2)
    _create(client, '', pending=1)
    _settle()
    assert [j['kind'] for j in queued_jobs] == ['journal.metadata']


# --- moving ------------------------------------------------------------------

def test_a_typed_meal_moves_to_the_food_log(client, run_jobs_sync, verdict):
    verdict['answer'] = {'kind': 'food', 'confident': True}
    entry_id = _create(client, 'had pho for lunch')

    assert client.get(f'/api/journal/{entry_id}').status_code == 404
    meal = _row('SELECT * FROM food_entries WHERE id=?', entry_id)
    assert meal['raw_content'] == 'had pho for lunch'
    assert _row('SELECT target_collection FROM journal_refiles WHERE entry_id=?',
                entry_id)['target_collection'] == 'food_entries'


def test_a_journal_verdict_titles_it_as_before(client, run_jobs_sync, verdict):
    entry_id = _create(client, 'thinking about the week')
    assert client.get(f'/api/journal/{entry_id}').get_json()['title'] == 'A title'
    assert _row('SELECT 1 FROM food_entries WHERE id=?', entry_id) is None


def test_a_photographed_meal_moves_with_its_photo(client, run_jobs_sync, verdict, vision):
    verdict['answer'] = {'kind': 'food', 'confident': True}
    entry_id, photo_id = str(ULID()), str(ULID())
    _create(client, '', pending=1, entry_id=entry_id)
    assert _upload(client, entry_id, photo_id).status_code == 201
    _settle()

    assert 'pho with basil' in verdict['prompts'][0]
    media = _row('SELECT * FROM food_media WHERE id=?', photo_id)
    assert media['entry_id'] == entry_id
    assert Path(media['path']).is_file()
    assert 'food' in Path(media['path']).parts
    # Read again with the food log's own prompt, not carried from the journal.
    assert media['description'] == 'Read again by the log.'
    assert _row('SELECT 1 FROM journal_attachments WHERE id=?', photo_id) is None


def test_a_receipt_moves_to_the_ledger(client, run_jobs_sync, verdict, vision):
    verdict['answer'] = {'kind': 'spending', 'confident': True}
    entry_id, photo_id = str(ULID()), str(ULID())
    _create(client, 'coffee with Sam, 4.50', pending=1, entry_id=entry_id)
    _upload(client, entry_id, photo_id, 'receipt.jpg')
    _settle()

    purchase = _row('SELECT * FROM spending_logs WHERE id=?', entry_id)
    assert purchase['raw_content'] == 'coffee with Sam, 4.50'
    assert purchase['date']
    assert _row('SELECT entry_id FROM spending_media WHERE id=?', photo_id)['entry_id'] == entry_id


def test_a_file_no_log_can_hold_keeps_it_in_the_journal(client, run_jobs_sync, verdict):
    verdict['answer'] = {'kind': 'food', 'confident': True}
    entry_id = str(ULID())
    _create(client, 'menu', pending=1, entry_id=entry_id)
    client.post(f'/api/journal/{entry_id}/attachments',
                data={'file': (io.BytesIO(b'%PDF-1.4'), 'menu.pdf', 'application/pdf'),
                      'attachmentId': str(ULID())},
                content_type='multipart/form-data')
    _settle()

    assert client.get(f'/api/journal/{entry_id}').status_code == 200
    assert verdict['prompts'] == []


def test_an_entry_something_points_at_stays(client, queued_jobs, verdict):
    verdict['answer'] = {'kind': 'food', 'confident': True}
    entry_id = _create(client, 'had pho for lunch')
    db = get_db()
    db.execute("INSERT INTO curated_tags(id, name, created_at) VALUES ('t1', 'meals', 0)")
    db.execute("INSERT INTO journal_entry_curated_tags(entry_id, tag_id) VALUES (?, 't1')", (entry_id,))
    db.commit()

    journal_routes.autofile_entry(entry_id)
    assert _row('SELECT 1 FROM journal_entries WHERE id=?', entry_id) is not None
    assert _row('SELECT 1 FROM food_entries WHERE id=?', entry_id) is None


def test_a_paused_model_leaves_the_job_pending(client, queued_jobs, verdict):
    from backend.ai import job_handlers  # noqa: F401  (registers the handlers)

    verdict['answer'] = service.InferencePaused('paused')
    entry_id = _create(client, 'had pho for lunch')
    queued_jobs.clear()
    db = get_db()
    db.execute("INSERT INTO llm_jobs(id, kind, target_id, payload, status, created_at)"
               " VALUES ('j1', 'journal.autofile', ?, '{}', 'pending', 0)", (entry_id,))
    db.commit()

    jobs.process_one('j1')
    assert _row("SELECT status FROM llm_jobs WHERE id='j1'")['status'] == 'pending'
    assert _row('SELECT 1 FROM journal_entries WHERE id=?', entry_id) is not None


# --- the phone's replays after a move -----------------------------------------

@pytest.fixture
def moved_meal(client, run_jobs_sync, verdict, vision):
    verdict['answer'] = {'kind': 'food', 'confident': True}
    entry_id, photo_id = str(ULID()), str(ULID())
    _create(client, 'pho', pending=1, entry_id=entry_id)
    _upload(client, entry_id, photo_id)
    _settle()
    assert _row('SELECT 1 FROM food_entries WHERE id=?', entry_id)
    return entry_id, photo_id


def test_a_replayed_create_does_not_bring_the_entry_back(client, moved_meal):
    entry_id, _ = moved_meal
    res = client.post('/api/journal', json={'id': entry_id, 'content': 'pho',
                                            'pendingAttachments': '1', 'autoFile': '1'})
    assert res.status_code == 201 and res.get_json()['id'] == entry_id
    assert _row('SELECT 1 FROM journal_entries WHERE id=?', entry_id) is None


def test_a_replayed_photo_is_acknowledged(client, moved_meal):
    entry_id, photo_id = moved_meal
    res = _upload(client, entry_id, photo_id)
    assert res.status_code == 201
    assert res.get_json() == {'id': photo_id, 'entryId': entry_id}
    assert get_db().execute('SELECT COUNT(*) AS n FROM food_media WHERE entry_id=?',
                            (entry_id,)).fetchone()['n'] == 1


def test_a_late_clip_lands_on_the_meal(client, moved_meal, monkeypatch):
    from backend.routes import stt as stt_routes
    monkeypatch.setattr(stt_routes, 'transcribe_file', lambda _p, **k: 'it was great')
    entry_id, _ = moved_meal
    clip_id = str(ULID())

    res = _clip(client, entry_id, clip_id)
    assert res.status_code == 201
    assert res.get_json() == {'id': entry_id, 'attachment': {'id': clip_id}}
    assert _row('SELECT 1 FROM journal_entries WHERE id=?', entry_id) is None
    clip = _row('SELECT * FROM food_media WHERE id=?', clip_id)
    assert clip['kind'] == 'audio' and clip['transcript'] == 'it was great'
    assert 'it was great' in _row('SELECT raw_content FROM food_entries WHERE id=?',
                                  entry_id)['raw_content']

    # And replaying it is a no-op.
    assert _clip(client, entry_id, clip_id).status_code == 201
