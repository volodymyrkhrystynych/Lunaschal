import base64
import pytest

PNG = b'\x89PNG\r\n\x1a\n' + b'test-image'

@pytest.fixture
def jobs_root(tmp_path, monkeypatch):
    monkeypatch.setenv('JOBS_ROOT', str(tmp_path / 'jobs'))

@pytest.fixture
def job(client):
    return client.post('/api/jobs', json={'title': 'Engineer', 'company': 'Acme'}).get_json()


def test_fill_run_records_page_fields_and_screenshot(client, job, jobs_root):
    app = client.post('/api/jobs/applications', json={'jobId': job['id']}).get_json()['id']
    response = client.post(f'/api/jobs/applications/{app}/fill-runs', json={
        'pageUrl': 'https://apply.example/step/2', 'pageTitle': 'Application',
        'fields': [{'label': 'Authorized?', 'answer': 'Yes', 'source': 'profile'}],
        'screenshotBase64': base64.b64encode(PNG).decode(),
    })
    assert response.status_code == 201
    detail = client.get(f'/api/jobs/applications/{app}').get_json()
    run = detail['fillRuns'][0]
    assert run['fields'][0]['answer'] == 'Yes'
    shot = client.get(run['screenshotUrl'])
    assert shot.data == PNG


def test_fill_run_rejects_non_png_screenshot(client, job):
    app = client.post('/api/jobs/applications', json={'jobId': job['id']}).get_json()['id']
    response = client.post(f'/api/jobs/applications/{app}/fill-runs', json={
        'fields': [], 'screenshotBase64': base64.b64encode(b'not png').decode(),
    })
    assert response.status_code == 400
