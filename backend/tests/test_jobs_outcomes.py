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


def test_ghosting_an_application_removes_it_from_the_waiting_list(client):
    """Closing one has to end its wait, or the panel never empties.

    `stale_applications` used to return `status IN ('submitted','ghosted')`,
    so `mark_ghosted_applications` closed an application at 60 days without
    removing it from "Waiting 10+ days" — whose own copy promises exactly that.
    Every application ever sent accumulated there: 795 rows, 732 of them
    already ghosted, the oldest waiting 5,716 days.
    """
    app_id = _application(client)
    now = int(time.time())
    db = get_db()
    db.execute("UPDATE applications SET status='submitted', applied_at=? WHERE id=?",
               (now - 61 * outcomes.DAY, app_id))
    db.commit()
    assert [r['id'] for r in outcomes.stale_applications(db, days=10, now=now)] == [app_id]

    outcomes.mark_ghosted_applications(db, now=now)

    assert outcomes.stale_applications(db, days=10, now=now) == []


def test_two_months_without_a_linked_reply_is_automatically_ghosted(client):
    app_id = _application(client)
    now = int(time.time())
    db = get_db()
    db.execute("UPDATE applications SET status='submitted', applied_at=? WHERE id=?",
               (now - 61 * outcomes.DAY, app_id))
    db.commit()

    assert outcomes.mark_ghosted_applications(db, now=now) == {'ghosted': 1}
    row = db.execute(
        'SELECT status, closed_at FROM applications WHERE id=?', (app_id,)
    ).fetchone()
    assert row['status'] == 'ghosted'
    assert row['closed_at'] == now
    event = db.execute(
        'SELECT status, source FROM application_status_events'
        ' WHERE application_id=? ORDER BY occurred_at DESC LIMIT 1', (app_id,),
    ).fetchone()
    assert dict(event) == {'status': 'ghosted', 'source': 'automatic'}


def test_automatic_ghosting_keeps_recent_replied_and_advanced_apps(client):
    old = _application(client)
    recent = _application(client)
    advanced = _application(client)
    now = int(time.time())
    db = get_db()
    for app_id, status, applied_at in (
        (old, 'submitted', now - 61 * outcomes.DAY),
        (recent, 'submitted', now - 59 * outcomes.DAY),
        (advanced, 'interview', now - 100 * outcomes.DAY),
    ):
        db.execute('UPDATE applications SET status=?, applied_at=? WHERE id=?',
                   (status, applied_at, app_id))
    # Any linked mail received after applying is a reply and prevents ghosting.
    db.execute(
        "INSERT INTO email_accounts (id, provider, email_address, created_at, updated_at)"
        " VALUES ('account', 'gmail', 'me@example.com', ?, ?)", (now, now),
    )
    db.execute(
        "INSERT INTO emails (id, account_id, provider_message_id, body_text, received_at, created_at)"
        " VALUES ('reply', 'account', 'reply-1', '', ?, ?)", (now - outcomes.DAY, now),
    )
    db.execute(
        "INSERT INTO job_email_links (id, application_id, email_id, confidence, created_at)"
        " VALUES ('link', ?, 'reply', 1, ?)", (old, now),
    )
    db.commit()

    assert outcomes.mark_ghosted_applications(db, now=now) == {'ghosted': 0}
    statuses = {
        r['id']: r['status'] for r in db.execute(
            'SELECT id, status FROM applications WHERE id IN (?, ?, ?)',
            (old, recent, advanced),
        )
    }
    assert statuses == {old: 'submitted', recent: 'submitted', advanced: 'interview'}


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
