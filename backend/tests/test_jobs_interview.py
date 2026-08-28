from backend.db.connection import get_db
from backend.jobs import interview


def test_schema_binds_star_mapping_to_real_bullet_ids():
    items = interview.schema(['b1', 'b2'])['properties']['questions']['items']
    assert items['properties']['storyBulletIds']['items']['enum'] == ['b1', 'b2']


def test_empty_profile_omits_story_ids_instead_of_leaving_them_unbounded():
    items = interview.schema([])['properties']['questions']['items']
    assert 'storyBulletIds' not in items['properties']


def test_generated_pack_resolves_only_real_stories(client, monkeypatch):
    job = client.post('/api/jobs', json={'title': 'Engineer', 'company': 'Acme'}).get_json()
    app = client.post('/api/jobs/applications', json={'jobId': job['id']}).get_json()['id']
    role = client.post('/api/jobs/profile/roles', json={'title': 'Dev'}).get_json()['id']
    bullet = client.post('/api/jobs/profile/bullets', json={
        'roleId': role, 'text': 'Recovered a failed migration.'
    }).get_json()['id']
    monkeypatch.setattr(interview, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(interview, 'chat_json', lambda *a, **k: {
        'roleSummary': 'Systems role', 'openingPitch': 'I build reliable systems.',
        'questions': [{'question': 'Tell me about a failure', 'kind': 'behavioral',
                       'whyAsked': 'Reliability', 'storyBulletIds': [bullet, 'fake'],
                       'gap': '', 'bridge': ''}],
        'questionsForThem': ['How is reliability measured?'], 'watchouts': [],
    })
    pack = interview.generate(get_db(), app, notes='First round covered Python.')
    assert pack['questions'][0]['storyBulletIds'] == [bullet]
    assert pack['questions'][0]['stories'][0]['text'] == 'Recovered a failed migration.'
    assert interview.latest(get_db(), app)['id'] == pack['id']
