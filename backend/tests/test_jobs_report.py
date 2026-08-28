from backend.db.connection import get_db
from backend.jobs import report


def test_report_is_self_contained_filterable_and_escaped(client):
    job = client.post('/api/jobs', json={
        'title': '<script>alert(1)</script>', 'company': 'Acme & Co',
        'url': 'https://example.com/job', 'description': 'A role',
    }).get_json()
    client.post('/api/jobs/applications', json={'jobId': job['id']})
    page = report.render(get_db())
    assert '<svg' in page and 'function draw()' in page
    assert 'https://' not in page.split('<script>')[0], 'no external assets'
    assert '<script>alert(1)</script>' not in page
    assert 'const DATA=' in page


def test_report_route_downloads_one_html_file(client):
    response = client.get('/api/jobs/report.html')
    assert response.status_code == 200
    assert response.mimetype == 'text/html'
    assert 'attachment' in response.headers['Content-Disposition']
