from pathlib import Path

from backend.db.connection import get_db
from backend.offline_knowledge import archive, tools


class FakeItem:
    content = memoryview(b'<html><body><h1>Moon</h1><p>A natural satellite.</p></body></html>')
    mimetype = 'text/html'


class FakeEntry:
    def __init__(self, path='Moon', title='Moon'):
        self.path = path
        self.title = title

    def get_item(self):
        return FakeItem()


class FakeArchive:
    article_count = 42
    has_fulltext_index = True

    def get_metadata(self, name):
        return {'Title': 'Test Wikipedia', 'Date': '2026-01', 'Language': 'eng'}.get(name, '')

    def get_entry_by_path(self, path):
        if path != 'Moon':
            raise KeyError(path)
        return FakeEntry()


class FakeQuery:
    def set_query(self, value):
        self.value = value
        return self


class FakeResults:
    def getEstimatedMatches(self):
        return 1

    def getResults(self, start, count):
        return iter(['Moon'])


class FakeSearcher:
    def __init__(self, zim):
        self.zim = zim

    def search(self, query):
        assert query.value == 'natural satellite'
        return FakeResults()


def _fake_library(monkeypatch, tmp_path):
    path = tmp_path / 'wikipedia.zim'
    path.write_bytes(b'zim')
    monkeypatch.setattr(archive, '_paths', lambda: [path])
    monkeypatch.setattr(archive, '_archive', lambda value: FakeArchive())
    monkeypatch.setattr(archive, '_libzim', lambda: (FakeArchive, FakeQuery, FakeSearcher))
    return path


def test_search_resolves_result_paths_to_titles(monkeypatch, tmp_path):
    _fake_library(monkeypatch, tmp_path)
    hits = archive.search('natural satellite')

    assert hits == [{
        'archiveId': archive.archive_id(tmp_path / 'wikipedia.zim'),
        'archiveTitle': 'Test Wikipedia',
        'archiveDate': '2026-01',
        'path': 'Moon',
        'title': 'Moon',
        'snippet': '',
    }]


def test_article_read_extracts_model_text(monkeypatch, tmp_path):
    path = _fake_library(monkeypatch, tmp_path)
    identifier = archive.archive_id(path)
    article = archive.read_article(identifier, 'Moon')

    assert article['title'] == 'Moon'
    assert 'A natural satellite.' in article['text']


def test_model_search_merges_queries_deduplicates_and_prefers_exact_titles(
        monkeypatch):
    def hit(path, title):
        return {
            'archiveId': 'wiki',
            'archiveTitle': 'Simple Wikipedia',
            'archiveDate': '2026-06',
            'path': path,
            'title': title,
            'snippet': '',
        }

    results = {
        'Charlie and the Chocolate Factory release year': [
            hit('Tom_and_Jerry', 'Tom and Jerry: Willy Wonka and the Chocolate Factory'),
            hit('Movie', 'Charlie and the Chocolate Factory (movie)'),
        ],
        'Charlie and the Chocolate Factory': [
            hit('Movie', 'Charlie and the Chocolate Factory (movie)'),
            hit('Book', 'Charlie and the Chocolate Factory'),
        ],
    }
    seen = []

    def fake_search(query, *, limit):
        seen.append((query, limit))
        return results[query]

    monkeypatch.setattr(archive, 'search', fake_search)
    text, event = archive.model_search(list(results))

    assert seen == [
        ('Charlie and the Chocolate Factory release year', 8),
        ('Charlie and the Chocolate Factory', 8),
    ]
    assert event['count'] == 3
    assert event['queries'] == list(results)
    assert text.count('archiveId=wiki path=Movie') == 1
    assert text.index('path=Book') < text.index('path=Movie') < text.index('path=Tom_and_Jerry')
    assert 'matched queries: Charlie and the Chocolate Factory' in text


def test_model_search_bounds_and_deduplicates_query_variants(monkeypatch):
    seen = []
    monkeypatch.setattr(archive, 'search', lambda query, *, limit: seen.append(query) or [])

    _text, event = archive.model_search([
        ' Moon ', 'moon', '', 'lunar body', 'natural satellite', 'fifth query',
    ])

    assert seen == ['Moon', 'lunar body', 'natural satellite', 'fifth query']
    assert event['queries'] == seen


def test_local_search_tool_offers_and_dispatches_batched_queries(monkeypatch):
    search_tool = next(
        tool for tool in tools.TOOLS
        if tool['function']['name'] == 'local_knowledge_search'
    )['function']
    assert search_tool['parameters']['required'] == ['queries']
    assert search_tool['parameters']['properties']['queries']['minItems'] == 2
    assert search_tool['parameters']['properties']['queries']['maxItems'] == 4

    seen = {}

    def fake_model_search(queries):
        seen['queries'] = queries
        return 'results', {'tool': 'local_knowledge_search', 'ok': True}

    monkeypatch.setattr(archive, 'model_search', fake_model_search)
    tools.run_tool('local_knowledge_search', {'queries': ['Moon', 'natural satellite']})

    assert seen['queries'] == ['Moon', 'natural satellite']


def test_tools_return_traceable_local_source(monkeypatch):
    monkeypatch.setattr(archive, 'read_article', lambda archive_id, path: {
        'title': 'Moon', 'archiveTitle': 'Test Wikipedia',
        'archiveDate': '2026-01', 'text': 'A natural satellite.',
    })
    text, event = tools.run_tool('local_knowledge_read', {
        'archiveId': 'abc', 'path': 'Moon',
    })

    assert 'Test Wikipedia (2026-01)' in text
    assert event['url'] == '/api/knowledge/archives/abc/content/Moon'
    assert event['sources'] == [{
        'url': '/api/knowledge/archives/abc/content/Moon', 'title': 'Moon',
    }]


def test_config_and_content_routes(client, monkeypatch, tmp_path):
    root = tmp_path / 'zims'
    root.mkdir()
    response = client.put('/api/knowledge/config', json={'path': str(root)})
    assert response.status_code == 200
    assert get_db().execute('SELECT knowledge_root FROM settings').fetchone()[0] == str(root)

    monkeypatch.setattr(archive, 'read_entry', lambda archive_id, path: (
        b'<h1>Safe</h1>', 'text/html', {},
    ))
    response = client.get('/api/knowledge/archives/abc/content/Page')
    assert response.status_code == 200
    assert "script-src 'none'" in response.headers['Content-Security-Policy']
    assert response.data == b'<h1>Safe</h1>'


def test_missing_library_dependency_is_a_visible_service_error(client, monkeypatch):
    def unavailable():
        raise archive.KnowledgeUnavailable('install libzim')

    monkeypatch.setattr(archive, 'list_archives', unavailable)
    response = client.get('/api/knowledge/archives')
    assert response.status_code == 503
    assert response.json['error'] == 'install libzim'
