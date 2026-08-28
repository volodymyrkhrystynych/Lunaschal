import time
from backend.db.connection import get_db
from backend.jobs import outcomes


def _application(client):
    job = client.post('/api/jobs', json={
        'title': 'Engineer', 'company': 'Acme', 'description': 'Build systems.'
    }).get_json()
    return client.post('/api/jobs/applications', json={'jobId': job['id']}).get_json()['id']


def test_stale_means_submitted_without_a_linked_reply(client):
    app_id = _application(client)
    old = int(time.time()) - 12 * outcomes.DAY
    db = get_db()
    db.execute("UPDATE applications SET status='submitted', applied_at=? WHERE id=?",
               (old, app_id))
    db.commit()
    rows = outcomes.stale_applications(db, days=10, now=int(time.time()))
    assert [row['id'] for row in rows] == [app_id]
    assert rows[0]['daysWaiting'] >= 11


def test_draft_uses_only_the_archived_submission(client, monkeypatch):
    app_id = _application(client)
    seen = {}
    monkeypatch.setattr(outcomes, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(outcomes, 'chat_json', lambda prompt, **kwargs:
                        seen.update(prompt=prompt, **kwargs) or
                        {'subject': 'Checking in', 'body': 'Hello.'})
    result = outcomes.draft_note(get_db(), app_id, context='Met Pat on Tuesday')
    assert result['body'] == 'Hello.'
    assert 'Build systems.' in seen['prompt']
    assert 'Met Pat on Tuesday' in seen['prompt']
    assert 'complete set of candidate claims' in seen['system']


def test_unknown_note_kind_never_calls_the_model(client, monkeypatch):
    app_id = _application(client)
    monkeypatch.setattr(outcomes, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(outcomes, 'chat_json', lambda *a, **k: (_ for _ in ()).throw(
        AssertionError('must not call')))
    assert outcomes.draft_note(get_db(), app_id, kind='sales_pitch') is None
