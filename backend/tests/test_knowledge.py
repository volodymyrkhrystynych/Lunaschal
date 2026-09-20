
import pytest

from backend.db.connection import get_db
from backend.offline_knowledge import archive, kinds, registry, tools


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
    """One stubbed ZIM. `results` maps a query to the titles it answers with."""

    def __init__(self, *, title='Test Wikipedia', date='2026-01', language='eng',
                 tags='', name='', creator='', ftindex=True, title_index=True,
                 results=None, article_count=42, uuid='uuid-0'):
        self.title = title
        self.date = date
        self.language = language
        self.tags = tags
        self.name = name
        self.creator = creator
        self.has_fulltext_index = ftindex
        self.has_title_index = title_index
        self.results = results or {}
        self.article_count = article_count
        self.uuid = uuid

    def get_metadata(self, key):
        return {
            'Title': self.title, 'Date': self.date, 'Language': self.language,
            'Tags': self.tags, 'Name': self.name, 'Creator': self.creator,
            'Flavour': '',
        }.get(key, '')

    def get_entry_by_path(self, path):
        return FakeEntry(path, path.replace('_', ' '))


class FakeQuery:
    def set_query(self, value):
        self.value = value
        return self


class FakeResults:
    def __init__(self, titles):
        self.titles = titles

    def getEstimatedMatches(self):
        return len(self.titles)

    def getResults(self, start, count):
        return iter(self.titles[start:start + count])


class FakeSearcher:
    """The fulltext path. Refuses to answer for an archive built without an
    index, which is exactly what real libzim does and the reason the title
    path had to exist."""

    def __init__(self, zim):
        self.zim = zim

    def search(self, query):
        assert self.zim.has_fulltext_index, 'fulltext search on a no-index archive'
        return FakeResults(list(self.zim.results.get(query.value, [])))


class FakeSuggestionSearcher:
    def __init__(self, zim):
        self.zim = zim

    def suggest(self, text):
        return FakeResults(list(self.zim.results.get(text, [])))


def fake_library(monkeypatch, tmp_path, specs):
    """Install a stub ZIM library and point the registry at it.

    `specs` is a list of `(filename, FakeArchive)`. Returns the dict of opens
    so a test can assert which archives were actually touched -- the
    "hundreds of DevDocs files are not all mmapped" property has no other
    observable.
    """
    root = tmp_path / 'zims'
    root.mkdir(exist_ok=True)
    paths, by_path, opens = [], {}, {}
    for filename, fake in specs:
        path = root / filename
        path.write_bytes(b'zim' * 10)
        paths.append(path)
        by_path[str(path)] = fake

    def open_archive(path):
        key = str(path)
        if key not in by_path:
            raise archive.ArchiveNotFound(key)
        fake = by_path[key]
        if fake is None:
            raise OSError('not a zim file')
        opens[key] = opens.get(key, 0) + 1
        return fake

    monkeypatch.setattr(archive, '_paths', lambda: sorted(paths, key=lambda p: p.name.lower()))
    monkeypatch.setattr(archive, '_archive', open_archive)
    monkeypatch.setattr(archive, '_libzim', lambda: (object, FakeQuery, FakeSearcher))
    monkeypatch.setattr(archive, '_suggestion', lambda: FakeSuggestionSearcher)

    db = get_db()
    db.execute('UPDATE settings SET knowledge_root=? WHERE id=1', (str(root),))
    db.commit()
    return {'root': root, 'paths': paths, 'opens': opens}


def wiki(**kwargs):
    kwargs.setdefault('tags', 'wikipedia;_category:wikipedia;_ftindex:yes')
    kwargs.setdefault('title', 'Test Wikipedia')
    return FakeArchive(**kwargs)


# --------------------------------------------------------------------------
# kinds.py -- pure classification
# --------------------------------------------------------------------------

@pytest.mark.parametrize('metadata, filename, expected_kind, expected_terms', [
    ({'Tags': 'wikipedia;_category:wikipedia;_ftindex:yes', 'Name': 'wikipedia_en-simple_all'},
     'wikipedia_en-simple_all_maxi_2026-06.zim', 'encyclopedia', 'wikipedia'),
    ({'Tags': 'devdocs;_ftindex:no', 'Name': 'devdocs_en_lit'},
     'devdocs_en_lit_2026-07.zim', 'docs', 'devdocs lit'),
    ({'Tags': '_category:stack_exchange;_ftindex:yes'},
     'stackoverflow.com_en_all_2026-07.zim', 'qa', 'stackoverflow'),
    ({}, 'math.stackexchange.com_en_all_2026-02.zim', 'qa', 'math stackexchange'),
    ({}, 'askubuntu.com_en_all_2026-06.zim', 'qa', 'askubuntu'),
    ({}, 'devdocs_en_rust_2026-07.zim', 'docs', 'devdocs rust'),
    ({}, 'gutenberg_en_all_2026-05.zim', 'other', 'gutenberg'),
])
def test_classify_derives_kind_and_terms(metadata, filename, expected_kind, expected_terms):
    kind, terms = kinds.classify(metadata, filename)
    assert (kind, terms) == (expected_kind, expected_terms)


def test_three_letter_library_name_is_not_eaten_as_a_language_code():
    # `lit` is Lit the framework and also looks exactly like an ISO-639-3 code.
    # Only the first language-shaped part is dropped, which is what saves it.
    _kind, terms = kinds.classify({}, 'devdocs_en_lit_2026-07.zim')
    assert 'lit' in terms.split()


def test_matches_query_needs_a_shared_token():
    assert kinds.matches_query('devdocs rust', kinds.query_tokens('rust lifetimes'))
    assert not kinds.matches_query('devdocs rust', kinds.query_tokens('moon landing'))
    assert not kinds.matches_query('', kinds.query_tokens('anything'))


# --------------------------------------------------------------------------
# registry.py
# --------------------------------------------------------------------------

def test_sync_records_kind_health_and_marks_a_vanished_archive_missing(
        monkeypatch, tmp_path):
    lib = fake_library(monkeypatch, tmp_path, [
        ('wikipedia_en_all_2026-06.zim', wiki()),
        ('devdocs_en_lit_2026-07.zim',
         FakeArchive(title='Lit', tags='devdocs;_ftindex:no', ftindex=False)),
    ])
    summary = registry.sync()
    assert summary['added'] == 2 and summary['rootAvailable'] is True

    rows = {r['filename']: r for r in registry.rows(enabled_only=False, healthy_only=False)}
    assert rows['wikipedia_en_all_2026-06.zim']['kind'] == 'encyclopedia'
    assert rows['wikipedia_en_all_2026-06.zim']['health'] == 'ok'
    # A DevDocs archive is healthy-but-title-only, not broken.
    assert rows['devdocs_en_lit_2026-07.zim']['kind'] == 'docs'
    assert rows['devdocs_en_lit_2026-07.zim']['health'] == 'no_fulltext'

    gone = lib['paths'][1]
    monkeypatch.setattr(archive, '_paths', lambda: [lib['paths'][0]])
    registry.sync()
    after = {r['filename']: r for r in registry.rows(enabled_only=False, healthy_only=False)}
    assert after[gone.name]['health'] == 'missing'


def test_sync_leaves_the_table_alone_when_the_root_is_unavailable(
        monkeypatch, tmp_path):
    fake_library(monkeypatch, tmp_path, [('wikipedia_en_all.zim', wiki())])
    registry.sync()
    db = get_db()
    db.execute('UPDATE settings SET knowledge_root=? WHERE id=1', ('/nope/not/mounted',))
    db.commit()

    summary = registry.sync()

    # An unplugged archive drive is not an emptied library.
    assert summary['rootAvailable'] is False
    assert registry.rows()[0]['health'] == 'ok'


def test_a_renamed_archive_keeps_its_disabled_flag(monkeypatch, tmp_path):
    lib = fake_library(monkeypatch, tmp_path, [
        ('wikipedia_en_all_2026-06.zim', wiki(uuid='stable-uuid')),
    ])
    registry.sync()
    original = registry.rows()[0]
    registry.set_enabled(original['id'], False)

    renamed = lib['root'] / 'wikipedia_en_all_2026-07.zim'
    renamed.write_bytes(b'zim' * 10)
    monkeypatch.setattr(archive, '_paths', lambda: [renamed])
    monkeypatch.setattr(archive, '_archive', lambda path: wiki(uuid='stable-uuid'))
    registry.sync()

    rows = registry.rows(enabled_only=False, healthy_only=False)
    adopted = [r for r in rows if r['filename'] == 'wikipedia_en_all_2026-07.zim'][0]
    assert adopted['enabled'] == 0


def test_a_user_set_kind_survives_a_rescan(monkeypatch, tmp_path):
    fake_library(monkeypatch, tmp_path, [('mystery_en_all.zim', FakeArchive(title='Mystery'))])
    registry.sync()
    ident = registry.rows()[0]['id']
    assert registry.rows()[0]['kind'] == 'other'

    registry.set_kind(ident, 'docs')
    registry.sync(force=True)

    row = registry.row(ident)
    assert (row['kind'], row['kind_source']) == ('docs', 'user')


def test_set_kind_refuses_a_kind_that_is_not_one(monkeypatch, tmp_path):
    fake_library(monkeypatch, tmp_path, [('wikipedia_en_all.zim', wiki())])
    registry.sync()
    with pytest.raises(ValueError):
        registry.set_kind(registry.rows()[0]['id'], 'encyclopaedia')


# --------------------------------------------------------------------------
# Federated search
# --------------------------------------------------------------------------

def test_a_huge_archive_cannot_starve_the_one_that_sorts_after_it(
        monkeypatch, tmp_path):
    """The regression this branch exists for.

    `askubuntu.` sorts before `wikipedia_`, and the old loop took results in
    filename order until the global limit was full -- so a Q&A dump with fifty
    matches returned all ten slots and the encyclopedia was never opened.
    """
    fake_library(monkeypatch, tmp_path, [
        ('askubuntu.com_en_all_2026-06.zim', FakeArchive(
            title='Ask Ubuntu', tags='_category:stack_exchange;_ftindex:yes',
            results={'install': [f'Thread_{i}' for i in range(50)]})),
        ('wikipedia_en_all_2026-06.zim', wiki(
            results={'install': ['Installation', 'Installer', 'Install_base']})),
    ])

    found = archive.search_many(['install'], limit=10)
    by_kind = {}
    for hit in found['results']:
        by_kind.setdefault(hit['archiveKind'], []).append(hit)

    assert len(by_kind.get('encyclopedia', [])) == 3, 'encyclopedia was starved'
    assert by_kind['qa'], 'the Q&A archive should still contribute'
    assert len(found['results']) == 10


def test_a_large_docs_collection_is_narrowed_rather_than_all_opened(
        monkeypatch, tmp_path):
    """Several hundred DevDocs archives must not all be mmapped per query."""
    specs = [('wikipedia_en_all_2026-06.zim', wiki(results={'rust': ['Rust_(programming_language)']}))]
    for i in range(30):
        specs.append((
            f'devdocs_en_lib{i:02d}_2026-07.zim',
            FakeArchive(title=f'Lib{i:02d}', tags='devdocs;_ftindex:no', ftindex=False,
                        article_count=100 - i, results={'rust': [f'Lib{i:02d}_page']}),
        ))
    lib = fake_library(monkeypatch, tmp_path, specs)
    # The scan opens everything once, by design. What must stay bounded is the
    # per-search cost, so measure from a clean slate.
    registry.sync()
    lib['opens'].clear()

    found = archive.search_many(['rust'], limit=10)

    docs_opened = [k for k in lib['opens'] if 'devdocs_' in k]
    assert len(docs_opened) <= archive.MAX_ARCHIVES_PER_CLASS
    assert any(h['archiveKind'] == 'encyclopedia' for h in found['results'])


def test_an_archive_without_a_fulltext_index_answers_by_title(
        monkeypatch, tmp_path):
    fake_library(monkeypatch, tmp_path, [
        ('devdocs_en_lit_2026-07.zim', FakeArchive(
            title='Lit', tags='devdocs;_ftindex:no', ftindex=False,
            results={'lit': ['ReactiveElement']})),
    ])

    found = archive.search_many(['lit'], limit=5)

    assert [h['title'] for h in found['results']] == ['ReactiveElement']
    assert found['results'][0]['matchKind'] == 'title'


def test_a_disabled_archive_is_never_opened(monkeypatch, tmp_path):
    lib = fake_library(monkeypatch, tmp_path, [
        ('wikipedia_en_all_2026-06.zim', wiki(results={'moon': ['Moon']})),
        ('askubuntu.com_en_all_2026-06.zim', FakeArchive(
            title='Ask Ubuntu', tags='_category:stack_exchange;_ftindex:yes',
            results={'moon': ['Moon_thread']})),
    ])
    registry.sync()
    ubuntu = [r for r in registry.rows() if r['kind'] == 'qa'][0]
    registry.set_enabled(ubuntu['id'], False)
    lib['opens'].clear()

    found = archive.search_many(['moon'], limit=5)

    assert all(h['archiveKind'] == 'encyclopedia' for h in found['results'])
    assert not any('askubuntu' in key for key in lib['opens'])


def test_one_unreadable_archive_does_not_fail_the_whole_search(
        monkeypatch, tmp_path):
    fake_library(monkeypatch, tmp_path, [
        ('aaa_broken_en_all.zim', None),
        ('wikipedia_en_all_2026-06.zim', wiki(results={'moon': ['Moon']})),
    ])

    found = archive.search_many(['moon'], limit=5)

    assert [h['title'] for h in found['results']] == ['Moon']
    # The scan classified it, so search never reaches it at all -- the cost of
    # a corrupt file is paid once, not on every query.
    broken = [r for r in registry.rows(enabled_only=False, healthy_only=False)
              if r['filename'] == 'aaa_broken_en_all.zim'][0]
    assert broken['health'] == 'unreadable'
    assert broken['health_error']


def test_an_archive_that_breaks_mid_search_is_skipped_not_fatal(
        monkeypatch, tmp_path):
    lib = fake_library(monkeypatch, tmp_path, [
        ('aaa_flaky_en_all.zim', wiki(title='Flaky', results={'moon': ['Moon_x']})),
        ('wikipedia_en_all_2026-06.zim', wiki(results={'moon': ['Moon']})),
    ])
    registry.sync()

    # Healthy at scan time, unreadable by the time the query arrives -- an
    # unplugged drive mid-session.
    def flaky(path):
        if 'flaky' in str(path):
            raise OSError('drive went away')
        return wiki(results={'moon': ['Moon']})

    monkeypatch.setattr(archive, '_archive', flaky)
    found = archive.search_many(['moon'], limit=5)

    assert [h['title'] for h in found['results']] == ['Moon']
    assert found['skipped'] == 1
    assert lib is not None


def test_each_archive_is_opened_once_for_every_query_variant(
        monkeypatch, tmp_path):
    lib = fake_library(monkeypatch, tmp_path, [
        ('wikipedia_en_all_2026-06.zim', wiki(results={
            'moon': ['Moon'], 'natural satellite': ['Natural_satellite'],
            'lunar body': ['Moon'],
        })),
    ])
    registry.sync()
    lib['opens'].clear()

    found = archive.search_many(['moon', 'natural satellite', 'lunar body'], limit=10)

    # Archive-outer, query-inner: four variants must not cost four visits.
    assert sum(lib['opens'].values()) == 1
    assert {h['title'] for h in found['results']} == {'Moon', 'Natural satellite'}


def test_a_hit_records_every_query_that_found_it(monkeypatch, tmp_path):
    fake_library(monkeypatch, tmp_path, [
        ('wikipedia_en_all_2026-06.zim', wiki(results={
            'moon': ['Moon'], 'lunar body': ['Moon'],
        })),
    ])
    found = archive.search_many(['moon', 'lunar body'], limit=5)
    assert found['results'][0]['_matches'] == ['moon', 'lunar body']


def test_search_facade_keeps_its_flat_shape(monkeypatch, tmp_path):
    lib = fake_library(monkeypatch, tmp_path, [
        ('wikipedia_en_all_2026-06.zim', wiki(results={'natural satellite': ['Moon']})),
    ])
    hits = archive.search('natural satellite')

    assert hits == [{
        'archiveId': archive.archive_id(lib['paths'][0]),
        'archiveTitle': 'Test Wikipedia',
        'archiveDate': '2026-01',
        'archiveKind': 'encyclopedia',
        'path': 'Moon',
        'title': 'Moon',
        'snippet': '',
        'matchKind': 'fulltext',
    }]


def test_article_read_extracts_model_text(monkeypatch, tmp_path):
    lib = fake_library(monkeypatch, tmp_path, [('wikipedia_en_all.zim', wiki())])
    registry.sync()
    identifier = archive.archive_id(lib['paths'][0])

    article = archive.read_article(identifier, 'Moon')

    assert article['title'] == 'Moon'
    assert 'A natural satellite.' in article['text']


def test_resolving_an_id_does_not_walk_the_filesystem(monkeypatch, tmp_path):
    lib = fake_library(monkeypatch, tmp_path, [('wikipedia_en_all.zim', wiki())])
    registry.sync()
    identifier = archive.archive_id(lib['paths'][0])

    walked = []
    monkeypatch.setattr(archive, '_paths', lambda: walked.append(1) or lib['paths'])
    assert archive._resolve(identifier) == lib['paths'][0]
    assert walked == [], '_resolve should be an indexed SELECT, not an rglob'


# --------------------------------------------------------------------------
# Model-facing tools
# --------------------------------------------------------------------------

def test_model_search_merges_queries_deduplicates_and_prefers_exact_titles(
        monkeypatch, tmp_path):
    fake_library(monkeypatch, tmp_path, [
        ('wikipedia_en_all_2026-06.zim', wiki(
            title='Simple Wikipedia', date='2026-06', results={
                'Charlie and the Chocolate Factory release year': [
                    'Tom and Jerry: Willy Wonka and the Chocolate Factory',
                    'Charlie and the Chocolate Factory (movie)',
                ],
                'Charlie and the Chocolate Factory': [
                    'Charlie and the Chocolate Factory (movie)',
                    'Charlie and the Chocolate Factory',
                ],
            })),
    ])

    text, event = archive.model_search([
        'Charlie and the Chocolate Factory release year',
        'Charlie and the Chocolate Factory',
    ])

    assert event['count'] == 3
    assert text.count('path=Charlie and the Chocolate Factory (movie)') == 1
    book = text.index('path=Charlie and the Chocolate Factory\n')
    movie = text.index('path=Charlie and the Chocolate Factory (movie)')
    tom = text.index('path=Tom and Jerry')
    assert book < movie < tom


def test_model_search_bounds_and_deduplicates_query_variants(monkeypatch):
    seen = []

    def fake_search_many(queries, **kwargs):
        seen.extend(queries)
        return {'results': [], 'searched': [], 'skipped': 0, 'tookMs': 1}

    monkeypatch.setattr(archive, 'search_many', fake_search_many)
    _text, event = archive.model_search([
        ' Moon ', 'moon', '', 'lunar body', 'natural satellite', 'fifth query',
    ])

    assert seen == ['Moon', 'lunar body', 'natural satellite', 'fifth query']
    assert event['queries'] == seen


def test_model_search_labels_the_source_kind_of_every_candidate(
        monkeypatch, tmp_path):
    fake_library(monkeypatch, tmp_path, [
        ('askubuntu.com_en_all_2026-06.zim', FakeArchive(
            title='Ask Ubuntu', date='2026-06',
            tags='_category:stack_exchange;_ftindex:yes',
            results={'grub': ['Repairing GRUB'], 'grub rescue': ['Repairing GRUB']})),
    ])
    text, _event = archive.model_search(['grub', 'grub rescue'])
    # An encyclopedia article and a Q&A thread answer differently-shaped
    # questions; the model cannot weigh that if it cannot see which it has.
    assert '· qa]' in text


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


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

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


def test_archives_route_reports_kind_and_health(client, monkeypatch, tmp_path):
    fake_library(monkeypatch, tmp_path, [
        ('devdocs_en_lit_2026-07.zim', FakeArchive(
            title='Lit', tags='devdocs;_ftindex:no', ftindex=False)),
    ])
    body = client.get('/api/knowledge/archives').get_json()

    assert len(body) == 1
    assert body[0]['kind'] == 'docs'
    assert body[0]['health'] == 'no_fulltext'
    assert body[0]['enabled'] is True


def test_rescan_and_patch_routes(client, monkeypatch, tmp_path):
    fake_library(monkeypatch, tmp_path, [('wikipedia_en_all.zim', wiki())])

    rescanned = client.post('/api/knowledge/archives/rescan').get_json()
    assert rescanned['total'] == 1
    ident = rescanned['archives'][0]['id']

    patched = client.patch(f'/api/knowledge/archives/{ident}',
                           json={'enabled': False, 'kind': 'docs'}).get_json()
    assert patched['enabled'] is False
    assert (patched['kind'], patched['kindSource']) == ('docs', 'user')

    assert client.patch('/api/knowledge/archives/nope', json={}).status_code == 404
    assert client.patch(f'/api/knowledge/archives/{ident}',
                        json={'kind': 'nonsense'}).status_code == 400


def test_search_route_returns_an_envelope_with_coverage(client, monkeypatch, tmp_path):
    fake_library(monkeypatch, tmp_path, [
        ('wikipedia_en_all_2026-06.zim', wiki(results={'moon': ['Moon']})),
    ])
    body = client.get('/api/knowledge/search?q=moon').get_json()

    assert body['results'][0]['title'] == 'Moon'
    assert body['results'][0]['archiveKind'] == 'encyclopedia'
    assert body['searched'] == 1 and body['skipped'] == 0
    # Internal ranking fields never cross the wire.
    assert not any(k.startswith('_') for k in body['results'][0])


@pytest.mark.parametrize('fulltext', [True, False])
def test_search_route_fills_limit_from_one_archive(client, monkeypatch, tmp_path, fulltext):
    fake_library(monkeypatch, tmp_path, [
        ('wikipedia_en_all.zim', wiki(
            ftindex=fulltext, results={'moon': [f'Moon_{i}' for i in range(50)]})),
    ])
    response = client.get('/api/knowledge/search?q=moon&limit=20')
    assert response.status_code == 200
    assert len(response.json['results']) == 20


def test_clearing_config_stops_search_but_preserves_registry(client, monkeypatch, tmp_path):
    monkeypatch.delenv('KNOWLEDGE_ROOT', raising=False)
    lib = fake_library(monkeypatch, tmp_path, [
        ('wikipedia_en_all.zim', wiki(results={'moon': ['Moon']})),
    ])
    registry.sync()
    ident = archive.archive_id(lib['paths'][0])
    registry.set_kind(ident, 'docs')
    lib['opens'].clear()

    assert client.put('/api/knowledge/config', json={'path': ''}).status_code == 200
    body = client.get('/api/knowledge/search?q=moon').get_json()
    assert body['results'] == []
    assert body['searched'] == 0
    assert archive.model_search(['moon'])[1]['count'] == 0
    assert lib['opens'] == {}
    assert registry.row(ident)['kind'] == 'docs'

    # Reconfiguring the library restores search and retains user corrections.
    client.put('/api/knowledge/config', json={'path': str(lib['root'])})
    assert client.get('/api/knowledge/search?q=moon').json['results'][0]['archiveKind'] == 'docs'


def test_search_route_can_be_scoped_to_one_kind(client, monkeypatch, tmp_path):
    fake_library(monkeypatch, tmp_path, [
        ('askubuntu.com_en_all_2026-06.zim', FakeArchive(
            title='Ask Ubuntu', tags='_category:stack_exchange;_ftindex:yes',
            results={'moon': ['Moon_thread']})),
        ('wikipedia_en_all_2026-06.zim', wiki(results={'moon': ['Moon']})),
    ])
    body = client.get('/api/knowledge/search?q=moon&kind=qa').get_json()

    assert [h['archiveKind'] for h in body['results']] == ['qa']


def test_missing_library_dependency_is_a_visible_service_error(client, monkeypatch):
    def unavailable():
        raise archive.KnowledgeUnavailable('install libzim')

    monkeypatch.setattr(archive, 'list_archives', unavailable)
    response = client.get('/api/knowledge/archives')
    assert response.status_code == 503
    assert response.json['error'] == 'install libzim'
