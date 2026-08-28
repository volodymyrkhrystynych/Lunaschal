import time
from backend.db.connection import get_db
from backend.jobs import upskill


def test_heatmap_prioritizes_frequent_missing_skills(client):
    client.post('/api/jobs/profile/skills', json={'name': 'Python'})
    for title, description in [
        ('One', 'Python Kubernetes Terraform'),
        ('Two', 'Kubernetes AWS'),
        ('Three', 'Kubernetes and Docker'),
    ]:
        client.post('/api/jobs', json={'title': title, 'description': description})
    plan = upskill.heatmap(get_db(), now=int(time.time()) + 1)
    assert plan['skills'][0]['term'] == 'kubernetes'
    assert plan['skills'][0]['postings'] == 3
    assert 'python' not in {skill['term'] for skill in plan['skills']}
    assert plan['skills'][0]['estimatedHours'] > 8


def test_resource_results_keep_source_urls(monkeypatch):
    monkeypatch.setattr(upskill.web, 'web_search', lambda *a, **k: [{
        'title': 'Official guide', 'url': 'https://example.com/guide', 'snippet': 'Docs'
    }])
    plan = {'skills': [{'term': 'kubernetes', 'resources': []}]}
    result = upskill.enrich(plan)
    assert result['skills'][0]['resources'][0]['url'] == 'https://example.com/guide'
    assert result['skills'][0]['resources'][0]['verifiedBy'] == 'configured web search'
