import time
from backend.db.connection import get_db
from backend.jobs import analytics, status


def test_skill_frequency_counts_postings_not_repetitions(client):
    now = int(time.time())
    client.post('/api/jobs', json={'title': 'One', 'description': 'Python Python React'})
    client.post('/api/jobs', json={'title': 'Two', 'description': 'Python and Kubernetes'})
    result = analytics.skill_frequency(get_db(), now=now + 1)
    counts = {row['term']: row['postings'] for row in result}
    assert counts['python'] == 2
    assert counts['react'] == 1


def test_funnel_uses_first_stage_event_for_response_time(client):
    job = client.post('/api/jobs', json={'title': 'Engineer'}).get_json()
    app = client.post('/api/jobs/applications', json={'jobId': job['id']}).get_json()['id']
    db = get_db()
    db.execute('UPDATE applications SET applied_at=? WHERE id=?', (1000, app))
    status.record(db, app, 'submitted', source='submission', at=1000)
    status.record(db, app, 'acknowledged', source='email', at=1000 + 2 * analytics.DAY)
    db.commit()
    metrics = analytics.funnel_metrics(db)
    assert metrics['responseRate'] == 1
    assert metrics['averageResponseDays'] == 2.0
