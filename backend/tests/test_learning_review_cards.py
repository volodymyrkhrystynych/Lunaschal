"""Review cards: an iPad notebook saved For review comes back as a learning
card with no back side, whose front is the entry's pages."""
import io
import time

import pytest
from PIL import Image
from ulid import ULID

from backend.day_boundary import day_bounds, day_key_for
from backend.db.connection import get_db
from backend.routes import journal as journal_routes


@pytest.fixture(autouse=True)
def _isolated_media_root(tmp_path, monkeypatch):
    monkeypatch.setenv('JOURNAL_ROOT', str(tmp_path / 'journal-media'))


@pytest.fixture(autouse=True)
def _no_background_work(monkeypatch):
    for name in ('_polish_bg', '_generate_metadata_bg'):
        monkeypatch.setattr(journal_routes, name, lambda *a, **k: None)


def _jpeg():
    buf = io.BytesIO()
    Image.new('RGB', (6, 8), (200, 200, 200)).save(buf, 'JPEG')
    return buf.getvalue()


def _create(client, *, for_review=True, **extra):
    id = str(ULID())
    body = {'id': id, 'content': '', 'pendingAttachments': 2, **extra}
    if for_review:
        body['forReview'] = True
    response = client.post('/api/journal', json=body)
    assert response.status_code == 201
    return id


def _page(client, entry_id, name):
    attachment_id = str(ULID())
    response = client.post(
        f'/api/journal/{entry_id}/attachments',
        data={'file': (io.BytesIO(_jpeg()), name, 'image/jpeg'), 'name': name,
              'attachmentId': attachment_id},
        content_type='multipart/form-data',
    )
    assert response.status_code in (200, 201), response.json
    return attachment_id


def _review_cards():
    return get_db().execute(
        "SELECT * FROM learning_cards WHERE kind='review' ORDER BY created_at").fetchall()


def _make_due(card_id):
    get_db().execute('UPDATE learning_cards SET due=? WHERE id=?', (int(time.time()) - 1, card_id))
    get_db().commit()


def _system_folder(client):
    return next(f for f in client.get('/api/learning/folders').json if f['system'])


def test_the_for_review_folder_is_always_there(client):
    folder = _system_folder(client)
    assert folder['name'] == 'For review'


def test_saving_for_review_makes_one_active_card_due_tomorrow(client):
    entry = _create(client)
    [card] = _review_cards()
    assert card['state'] == 'active'
    assert card['source_type'] == 'journal_review' and card['source_id'] == entry
    assert card['folder_id'] == _system_folder(client)['id']
    assert card['due'] == day_bounds(day_key_for(int(time.time())))[1]
    # Not due today, so it is not in the deck yet.
    assert client.get('/api/learning/due').json == []


def test_a_replayed_create_makes_no_second_card(client):
    entry = _create(client)
    client.post('/api/journal', json={'id': entry, 'content': '', 'pendingAttachments': 2,
                                      'forReview': True})
    assert len(_review_cards()) == 1


def test_the_ipads_string_flag_counts(client):
    # JournalAPI.entryBody sends every field as a string.
    _create(client, for_review=False, forReview='true')
    assert len(_review_cards()) == 1


def test_without_the_switch_there_is_no_card(client):
    _create(client, for_review=False)
    assert _review_cards() == []


def test_the_due_card_carries_its_pages_in_order(client):
    entry = _create(client)
    first = _page(client, entry, 'Notes p1.jpg')
    second = _page(client, entry, 'Notes p2.jpg')
    get_db().execute("UPDATE journal_entries SET title='Ferns' WHERE id=?", (entry,))
    get_db().commit()
    [row] = _review_cards()
    _make_due(row['id'])

    [card] = client.get('/api/learning/due').json
    assert card['kind'] == 'review'
    assert card['review']['entryId'] == entry
    assert card['review']['title'] == 'Ferns'
    assert [p['id'] for p in card['review']['pages']] == [first, second]
    assert card['review']['pages'][0]['url'] == f'/api/journal/attachments/{first}/file'
    assert client.get(card['review']['pages'][0]['url']).status_code == 200
    # The browse list and the single-card read carry the same.
    assert client.get(f"/api/learning/cards/{row['id']}").json['review']['pages'][1]['id'] == second


def test_a_review_card_is_rated_without_an_attempt(client):
    _create(client)
    [row] = _review_cards()
    _make_due(row['id'])
    response = client.post(f"/api/learning/cards/{row['id']}/review",
                           json={'rating': 3, 'reviewId': str(ULID()), 'answerMode': 'self'})
    assert response.status_code == 200
    assert client.get('/api/learning/due').json == []
    review = get_db().execute('SELECT * FROM learning_reviews WHERE card_id=?', (row['id'],)).fetchone()
    assert review['answer_mode'] == 'self'


def test_routes_that_need_an_answer_refuse_a_review_card(client):
    _create(client)
    [row] = _review_cards()
    id = row['id']
    assert client.post('/api/learning/attempts',
                       json={'cardId': id, 'mode': 'answered', 'answer': 'x'}).status_code == 400
    assert client.post(f'/api/learning/cards/{id}/revise', json={'answer': 'x'}).status_code == 400
    assert client.post(f'/api/learning/cards/{id}/chat', json={'message': 'x'}).status_code == 400
    assert client.post(f'/api/learning/cards/{id}/verify').status_code == 400


def test_the_for_review_folder_cannot_be_renamed_or_deleted(client):
    folder = _system_folder(client)
    assert client.patch(f"/api/learning/folders/{folder['id']}", json={'name': 'Other'}).status_code == 409
    assert client.delete(f"/api/learning/folders/{folder['id']}").status_code == 409
    assert _system_folder(client)['name'] == 'For review'
    # Ordinary folders are unaffected.
    other = client.post('/api/learning/folders', json={'name': 'Mine'}).json['id']
    assert client.delete(f'/api/learning/folders/{other}').status_code == 200


def test_deleting_the_entry_deletes_its_card(client):
    entry = _create(client)
    assert client.delete(f'/api/journal/{entry}').status_code == 200
    assert _review_cards() == []


def test_a_refiled_notebook_keeps_its_card_and_schedule(client):
    morning = _create(client)
    [card] = _review_cards()
    get_db().execute("UPDATE learning_cards SET fsrs_state='{\"s\":1}', due=123 WHERE id=?", (card['id'],))
    get_db().commit()

    evening = _create(client, replaces=morning)
    [moved] = _review_cards()
    assert moved['id'] == card['id']
    assert moved['source_id'] == evening
    assert moved['due'] == 123 and moved['fsrs_state'] == '{"s":1}'


def test_a_refile_without_the_switch_drops_the_card(client):
    morning = _create(client)
    _create(client, for_review=False, replaces=morning)
    assert _review_cards() == []


def test_an_existing_folder_called_for_review_is_adopted(client):
    from backend.learning.review_cards import ensure_review_folder
    db = get_db()
    db.execute('UPDATE learning_folders SET system_key=NULL, name=? WHERE system_key=?', ('for review', 'review'))
    db.commit()
    [mine] = db.execute('SELECT id FROM learning_folders').fetchall()
    assert ensure_review_folder(db) == mine[0]
    db.commit()
    assert len(db.execute('SELECT id FROM learning_folders').fetchall()) == 1
