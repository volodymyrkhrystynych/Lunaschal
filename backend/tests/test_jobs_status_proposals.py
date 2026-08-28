import time
from ulid import ULID
from backend.db.connection import get_db
from backend.jobs import linker


def test_linked_email_proposes_then_applies_with_source_citation(client):
    job = client.post('/api/jobs', json={'title': 'Engineer', 'company': 'Acme'}).get_json()
    app = client.post('/api/jobs/applications', json={'jobId': job['id']}).get_json()['id']
    client.post(f'/api/jobs/applications/{app}/submit', json={})
    db = get_db(); now = int(time.time()); email = str(ULID()); account = str(ULID())
    db.execute("INSERT INTO email_accounts (id, provider, email_address, created_at, updated_at) VALUES (?, 'gmail', 'me@example.com', ?, ?)", (account, now, now))
    db.execute("INSERT INTO emails (id, account_id, provider_message_id, subject, sender, sender_email, body_text, received_at, category, job_status, created_at) VALUES (?, ?, ?, 'Interview at Acme', 'Recruiter', 'recruiter@acme.test', '', ?, 'job_application', 'interview_next_step', ?)",
               (email, account, email, now, now))
    linker.link(db, app, email, 1, now=now)
    proposals = linker.status_proposals(db)
    assert proposals[0]['proposedStatus'] == 'interview'
    assert proposals[0]['source']['subject'] == 'Interview at Acme'
    response = client.post('/api/jobs/linkage/status-proposals/apply', json={
        'proposals': [{'applicationId': app, 'emailId': email}]
    })
    assert response.get_json()['applied'][0]['status'] == 'interview'
    assert linker.status_proposals(db) == []
