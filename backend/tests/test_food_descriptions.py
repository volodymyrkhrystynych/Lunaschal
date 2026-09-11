import io

import pytest

from backend.ai import images, jobs, job_handlers  # noqa: F401
from backend.db.connection import get_db
from backend.routes import food


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv('FOOD_ROOT', str(tmp_path / 'food'))
    monkeypatch.setattr(images, 'is_vision_configured', lambda: True)
    monkeypatch.setattr(images, 'describe_image', lambda path, **kw: 'The menu says Pad Thai.')
    monkeypatch.setattr(food, 'check_homemade_recipe_match', lambda _: None)
    monkeypatch.setattr(food, 'parse_food_entry', lambda text, **kw: {
        'notes': text.replace('pad tie', 'pad thai') if kw.get('descriptions') else text,
    })


def drain():
    for _ in range(20):
        if jobs.drain_once() is None:
            return
    pytest.fail('Food jobs did not finish')


def create(client, **kw):
    return client.post('/api/food', json={'text': 'I had pad tie.', **kw}).get_json()


def photo(client, entry_id):
    return client.post(f'/api/food/{entry_id}/media', data={
        'media': (io.BytesIO(b'photo'), 'meal.jpg'),
    }).get_json()['media'][0]['id']


def get(client, entry):
    return client.get(f"/api/food/{entry['id']}").get_json()


def test_late_photo_repolishes_generated_notes_and_keeps_raw(client):
    entry = create(client)
    drain()
    assert get(client, entry)['notes'] == 'I had pad tie.'
    photo(client, entry['id'])
    assert get(client, entry)['media'][0]['descriptionStatus'] == 'running'
    drain()
    result = get(client, entry)
    assert result['notes'] == 'I had pad thai.'
    assert result['rawContent'] == 'I had pad tie.'
    assert result['media'][0]['description'] == 'The menu says Pad Thai.'
    assert not result['polishing']


def test_manual_notes_survive_late_description(client):
    entry = create(client)
    drain()
    client.patch(f"/api/food/{entry['id']}", json={'notes': 'My own correction.'})
    photo(client, entry['id'])
    drain()
    assert get(client, entry)['notes'] == 'My own correction.'
    # An explicit Polish action can update an existing/manual note.
    assert client.post(f"/api/food/{entry['id']}/polish").status_code == 200
    assert get(client, entry)['notes'] == 'I had pad thai.'


def test_queued_structure_uses_latest_accumulated_transcript(client):
    entry = create(client)
    photo(client, entry['id'])
    db = get_db()
    food._append_entry_text(db, entry['id'], 'Then tea.')
    db.commit()
    drain()
    assert get(client, entry)['notes'] == 'I had pad thai.\n\nThen tea.'


def test_description_failure_is_visible_and_retryable(client, monkeypatch):
    entry = create(client)
    media_id = photo(client, entry['id'])
    def fail(*a, **kw):
        raise images.VisionUnavailable('Model offline')
    monkeypatch.setattr(images, 'describe_image', fail)
    drain()
    result = get(client, entry)
    assert result['media'][0]['descriptionStatus'] == 'error'
    assert result['media'][0]['descriptionError'] == 'Model offline'
    assert result['notes'] == 'I had pad tie.'
    monkeypatch.setattr(images, 'describe_image', lambda *a, **kw: 'Pad Thai')
    assert client.post(f'/api/food/media/{media_id}/describe').status_code == 202
    drain()
    assert get(client, entry)['notes'] == 'I had pad thai.'


def test_paused_description_stays_pending(client, monkeypatch):
    from backend.ai.service import InferencePaused
    entry = create(client)
    media_id = photo(client, entry['id'])
    def pause(*a, **kw):
        raise InferencePaused('paused')
    monkeypatch.setattr(images, 'describe_image', pause)
    job = get_db().execute("SELECT id FROM llm_jobs WHERE kind='food.describe_media'").fetchone()
    assert jobs.process_one(job['id'])['error'] == 'paused'
    assert get_db().execute('SELECT status FROM llm_jobs WHERE id=?', (job['id'],)).fetchone()['status'] == 'pending'
    assert get(client, entry)['media'][0]['descriptionStatus'] == 'running'
    monkeypatch.setattr(images, 'describe_image', lambda *a, **kw: 'Pad Thai')
    drain()
    assert get(client, entry)['media'][0]['id'] == media_id
    assert get(client, entry)['notes'] == 'I had pad thai.'


def test_polish_failure_preserves_existing_notes(client, monkeypatch):
    entry = create(client, notes='My note')
    monkeypatch.setattr(food, 'parse_food_entry', lambda *a, **kw: None)
    assert client.post(f"/api/food/{entry['id']}/polish").status_code == 503
    assert get(client, entry)['notes'] == 'My note'


def test_failed_redescription_does_not_undo_a_successful_polish(client, monkeypatch):
    entry = create(client)
    media_id = photo(client, entry['id'])
    drain()
    assert get(client, entry)['notes'] == 'I had pad thai.'
    def fail(*a, **kw):
        raise images.VisionUnavailable('Model offline')
    monkeypatch.setattr(images, 'describe_image', fail)
    client.post(f'/api/food/media/{media_id}/describe')
    drain()
    result = get(client, entry)
    assert result['notes'] == 'I had pad thai.'
    assert result['media'][0]['description'] == 'The menu says Pad Thai.'


def test_photo_upload_replay_does_not_repeat_description(client):
    def upload():
        return client.post('/api/food', data={
            'id': 'meal-replay', 'text': 'I had pad tie.',
            'mediaIds': '["photo-replay"]',
            'media': (io.BytesIO(b'photo'), 'meal.jpg'),
        })
    assert upload().status_code == 201
    drain()
    assert upload().status_code == 201
    drain()
    assert get_db().execute(
        "SELECT COUNT(*) FROM llm_jobs WHERE kind='food.describe_media'"
    ).fetchone()[0] == 1


def test_vision_off_keeps_photo_available_for_manual_description(client, monkeypatch):
    monkeypatch.setattr(images, 'is_vision_configured', lambda: False)
    entry = create(client)
    media_id = photo(client, entry['id'])
    drain()
    assert get(client, entry)['media'][0]['descriptionStatus'] == 'idle'
    assert client.post(f'/api/food/media/{media_id}/describe').status_code == 202
    drain()
    assert get(client, entry)['notes'] == 'I had pad thai.'


def test_manual_edit_during_inference_wins(client, monkeypatch):
    entry = create(client)
    def parse(*a, **kw):
        client.patch(f"/api/food/{entry['id']}", json={'notes': 'Edited while polishing'})
        return {'notes': 'AI correction'}
    monkeypatch.setattr(food, 'parse_food_entry', parse)
    drain()
    assert get(client, entry)['notes'] == 'Edited while polishing'


def test_parser_receives_photo_context_separately_from_transcript(monkeypatch):
    from backend.ai import food as ai_food
    seen = {}
    monkeypatch.setattr(ai_food, 'is_ai_configured', lambda: True)
    def chat(prompt, **kw):
        seen.update(prompt=prompt, **kw)
        return {'notes': 'I had pad thai.'}
    monkeypatch.setattr(ai_food, 'chat_json', chat)
    ai_food.parse_food_entry('I had pad tie.', memory='Favourite place: Kinton',
                             descriptions='The menu says Pad Thai.')
    assert seen['prompt'].startswith('I had pad tie.\n\n---\nContext:')
    assert 'Favourite place: Kinton' in seen['prompt']
    assert 'The menu says Pad Thai.' in seen['prompt']
    assert 'Keep the spoken wording when a correction is uncertain' in seen['system']


def test_descriptions_migration_is_idempotent():
    import sqlite3
    from backend.db.connection import _ensure_food_descriptions
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE food_entries(id TEXT, notes TEXT)')
    db.execute('CREATE TABLE food_media(id TEXT)')
    db.execute("INSERT INTO food_entries VALUES ('meal', 'Keep this')")
    _ensure_food_descriptions(db)
    _ensure_food_descriptions(db)
    assert db.execute('SELECT notes, generated_notes FROM food_entries').fetchone() == ('Keep this', None)
    assert {'description', 'description_status', 'description_error'} <= {
        r[1] for r in db.execute('PRAGMA table_info(food_media)')
    }
    db.close()
