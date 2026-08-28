from backend.db.connection import get_db
from backend.jobs import career_watch


def test_posting_links_are_deduplicated_and_resolved():
    html = '''<a href="/careers">Careers</a>
              <a href="/jobs/123#top">Backend Engineer</a>
              <a href="/jobs/123#apply">Apply to this job</a>
              <a href="/about">About</a>'''
    assert career_watch.posting_links(html, 'https://acme.example/careers') == [
        'https://acme.example/jobs/123'
    ]


def test_watch_baselines_then_surfaces_only_new_links(client, monkeypatch):
    pages = [
        '<a href="/jobs/one">Job one</a>',
        '<a href="/jobs/one">Job one</a><a href="/jobs/two">Job two</a>',
    ]
    monkeypatch.setattr(career_watch.ingest, 'fetch_html',
                        lambda url: (pages.pop(0), 'https://acme.example/careers'))
    watch = career_watch.create(get_db(), 'https://acme.example/careers', 'Acme')
    monkeypatch.setattr(career_watch.ingest, 'ingest_url', lambda url: {
        'title': 'New engineer', 'company': 'Acme', 'description': 'Python',
        'url': url,
    })
    result = career_watch.run(get_db(), watch['id'])
    assert result['new'] == 1
    assert result['added'] == 1
    assert get_db().execute('SELECT COUNT(*) c FROM jobs').fetchone()['c'] == 1
