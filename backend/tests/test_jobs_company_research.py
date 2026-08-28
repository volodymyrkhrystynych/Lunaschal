from backend.db.connection import get_db
from backend.jobs import company_research


def test_schema_binds_citations_to_fetched_sources():
    enum = company_research.schema(3)['properties']['facts']['items']['properties']['sourceIndexes']['items']['enum']
    assert enum == [0, 1, 2]


def test_invalid_or_uncited_claims_are_dropped(client, monkeypatch):
    job = client.post('/api/jobs', json={'title': 'Engineer', 'company': 'Acme'}).get_json()
    app = client.post('/api/jobs/applications', json={'jobId': job['id']}).get_json()['id']
    monkeypatch.setattr(company_research, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(company_research.web, 'is_search_configured', lambda: True)
    monkeypatch.setattr(company_research.web, 'web_search', lambda *a, **k: [
        {'title': 'Acme', 'url': 'https://example.com/acme', 'snippet': ''}])
    monkeypatch.setattr(company_research.web, 'web_fetch', lambda url: {
        'title': 'Acme engineering', 'url': url, 'text': 'Acme builds databases.'})
    monkeypatch.setattr(company_research, 'chat_json', lambda *a, **k: {
        'facts': [{'claim': 'Builds databases', 'sourceIndexes': [0]},
                  {'claim': 'Invented', 'sourceIndexes': [9]},
                  {'claim': 'Uncited', 'sourceIndexes': []}],
        'interviewAngles': ['Ask about databases'],
    })
    result = company_research.research(get_db(), app, interviewer='Pat Lee')
    assert [fact['claim'] for fact in result['facts']] == ['Builds databases']
    assert result['facts'][0]['sources'][0]['url'] == 'https://example.com/acme'
    assert company_research.latest(get_db(), app)['id'] == result['id']
