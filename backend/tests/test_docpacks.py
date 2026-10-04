"""Library documentation packages (backend/offline_knowledge/docpacks.py)."""
import io
import sqlite3

import pytest
import requests

from backend.db.connection import get_db
from backend.offline_knowledge import archive, docpacks, tools

from backend.tests.test_knowledge import fake_library, wiki

FLASK_CHUNKS = [
    ('docs/blueprints.md', 'Modular Applications with Blueprints', 'Why Blueprints?',
     'Blueprints in Flask are intended for these cases: factor an application into '
     'a set of blueprints.'),
    ('docs/blueprints.md', 'Modular Applications with Blueprints', 'Registering Blueprints',
     'To register a blueprint call app.register_blueprint(simple_page).'),
    ('docs/blueprints.md', 'Modular Applications with Blueprints', 'Blueprint Resources',
     'Blueprints can provide resources such as static files and templates.'),
    ('docs/config.md', 'Configuration Handling', 'Configuration Basics',
     'The config is actually a subclass of a dictionary: app.config["TESTING"] = True.'),
]


def build_pack(path, name='flask', version='3.1.3', chunks=FLASK_CHUNKS, **meta):
    """A package built with exactly the schema neuledge/context writes."""
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript('''
      CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
      CREATE TABLE chunks (
        id INTEGER PRIMARY KEY,
        doc_path TEXT NOT NULL,
        doc_title TEXT NOT NULL,
        section_title TEXT NOT NULL,
        content TEXT NOT NULL,
        tokens INTEGER NOT NULL,
        has_code INTEGER DEFAULT 0
      );
      CREATE VIRTUAL TABLE chunks_fts USING fts5(
        doc_title, section_title, content,
        content='chunks', content_rowid='id',
        tokenize='porter unicode61'
      );
    ''')
    values = {'name': name, 'version': version, **meta}
    conn.executemany('INSERT INTO meta (key, value) VALUES (?, ?)', values.items())
    conn.executemany(
        'INSERT INTO chunks (doc_path, doc_title, section_title, content, tokens, has_code)'
        ' VALUES (?, ?, ?, ?, ?, 0)',
        [(*c, len(c[3]) // 4) for c in chunks],
    )
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def root(monkeypatch, tmp_path):
    folder = tmp_path / 'docpacks'
    monkeypatch.setenv('DOCPACKS_ROOT', str(folder))
    return folder


def upload(root, tmp_path, **kwargs):
    source = build_pack(tmp_path / f"src-{kwargs.get('name', 'flask')}.db", **kwargs)
    with source.open('rb') as fh:
        return docpacks.import_file(fh)


class FakeResponse:
    def __init__(self, body=b'', status=200, payload=None):
        self.body = body
        self.status_code = status
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'{self.status_code}')

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]

    def json(self):
        return self.payload


# --------------------------------------------------------------------------
# validate / import
# --------------------------------------------------------------------------

def test_validate_reads_meta_and_counts_chunks(tmp_path):
    path = build_pack(tmp_path / 'p.db', description='Web framework',
                      source_url='https://github.com/pallets/flask')
    meta = docpacks.validate(path)
    assert meta == {
        'name': 'flask', 'version': '3.1.3', 'description': 'Web framework',
        'source_url': 'https://github.com/pallets/flask', 'chunk_count': 4,
    }


def test_validate_rejects_garbage_and_wrong_shapes(tmp_path):
    garbage = tmp_path / 'garbage.db'
    garbage.write_bytes(b'not a database at all' * 100)
    with pytest.raises(docpacks.DocPackError):
        docpacks.validate(garbage)

    other = tmp_path / 'other.db'
    conn = sqlite3.connect(other)
    conn.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)')
    conn.execute('CREATE TABLE chunks (id INTEGER PRIMARY KEY, content TEXT)')
    conn.commit()
    conn.close()
    with pytest.raises(docpacks.DocPackError, match='chunks_fts'):
        docpacks.validate(other)

    nameless = build_pack(tmp_path / 'nameless.db', name='')
    with pytest.raises(docpacks.DocPackError, match='name or version'):
        docpacks.validate(nameless)


def test_import_adopts_the_file_and_reinstall_keeps_the_row(root, tmp_path):
    pack = upload(root, tmp_path)
    assert pack['registry'] == 'local' and pack['chunk_count'] == 4
    assert (root / pack['filename']).is_file()
    docpacks.set_enabled(pack['id'], False)

    again = upload(root, tmp_path)
    assert again['id'] == pack['id']
    assert again['enabled'] == 0
    assert len(docpacks.rows()) == 1
    assert not list(root.glob('*.part'))


def test_a_rejected_import_leaves_nothing_behind(root):
    with pytest.raises(docpacks.DocPackError):
        docpacks.import_file(io.BytesIO(b'definitely not sqlite' * 50))
    assert docpacks.rows() == []
    assert not [p for p in root.iterdir()]


def test_delete_removes_file_and_row(root, tmp_path):
    pack = upload(root, tmp_path)
    assert docpacks.delete(docpacks.public_id(pack['id']))
    assert not (root / pack['filename']).exists()
    assert docpacks.rows() == []
    assert not docpacks.delete(pack['id'])


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

def test_install_downloads_validates_and_records(root, tmp_path, monkeypatch):
    body = build_pack(tmp_path / 'remote.db').read_bytes()
    seen = {}

    def fake_get(url, **kwargs):
        seen['url'] = url
        return FakeResponse(body)

    monkeypatch.setattr(docpacks.requests, 'get', fake_get)
    pack = docpacks.install('pip', 'flask', '3.1.3')
    assert seen['url'] == f'{docpacks.REGISTRY_URL}/packages/pip/flask/3.1.3/download'
    assert (pack['registry'], pack['name'], pack['version']) == ('pip', 'flask', '3.1.3')
    assert (root / pack['filename']).is_file()


def test_install_escapes_scoped_names(root, tmp_path, monkeypatch):
    body = build_pack(tmp_path / 'remote.db', name='@trpc/server', version='11.0.0').read_bytes()
    seen = {}
    monkeypatch.setattr(docpacks.requests, 'get',
                        lambda url, **kw: seen.setdefault('url', url) and FakeResponse(body))
    pack = docpacks.install('npm', '@trpc/server', '11.0.0')
    assert seen['url'].endswith('/packages/npm/%40trpc%2Fserver/11.0.0/download')
    assert '/' not in pack['filename']


def test_a_bad_download_leaves_no_row_and_no_part(root, monkeypatch):
    monkeypatch.setattr(docpacks.requests, 'get',
                        lambda url, **kw: FakeResponse(b'<html>error page</html>'))
    with pytest.raises(docpacks.DocPackError):
        docpacks.install('pip', 'flask', '3.1.3')
    assert docpacks.rows() == []
    assert not list(root.glob('.*.part'))

    def offline(url, **kw):
        raise requests.ConnectionError('no route')

    monkeypatch.setattr(docpacks.requests, 'get', offline)
    with pytest.raises(docpacks.RegistryUnavailable):
        docpacks.install('pip', 'flask', '3.1.3')
    assert not list(root.glob('.*.part'))


@pytest.mark.parametrize('registry, name, version', [
    ('pip', '../etc', '1.0'),
    ('PIP;rm', 'flask', '1.0'),
    ('pip', 'flask', '1.0/../../x'),
    ('pip', '', '1.0'),
])
def test_install_refuses_specs_that_could_escape_the_url(registry, name, version, root):
    with pytest.raises(docpacks.DocPackError):
        docpacks.install(registry, name, version)


def test_registry_search_marks_installed_versions(root, tmp_path, monkeypatch):
    body = build_pack(tmp_path / 'remote.db').read_bytes()
    monkeypatch.setattr(docpacks.requests, 'get', lambda url, **kw: FakeResponse(body))
    docpacks.install('pip', 'flask', '3.1.3')

    payload = [
        {'registry': 'pip', 'name': 'flask', 'version': '3.1.3', 'size': 864256},
        {'registry': 'pip', 'name': 'flask', 'version': '3.0.3', 'size': 851968},
    ]
    monkeypatch.setattr(docpacks.requests, 'get',
                        lambda url, **kw: FakeResponse(payload=payload))
    found = docpacks.registry_search('pip', 'flask')
    assert [(f['version'], f['installed']) for f in found] == [('3.1.3', True), ('3.0.3', False)]


# --------------------------------------------------------------------------
# search and read
# --------------------------------------------------------------------------

def test_search_pack_ranks_section_titles_and_returns_chunk_paths(root, tmp_path):
    pack = upload(root, tmp_path)
    hits = docpacks.search_pack(pack, 'register blueprint', 5)
    assert hits[0][1] == 'Modular Applications with Blueprints — Registering Blueprints'
    assert hits[0][0] == '2'
    # Punctuation in a model query must not become FTS syntax.
    assert docpacks.search_pack(pack, 'app.config["TESTING"]', 5)
    assert docpacks.search_pack(pack, '"', 5) == []


def test_read_returns_the_section_with_its_neighbours_in_order(root, tmp_path):
    pack = upload(root, tmp_path)
    article = docpacks.read(docpacks.public_id(pack['id']), '2')
    assert article['title'].endswith('Registering Blueprints')
    text = article['text']
    assert text.index('factor an application') < text.index('register_blueprint') \
        < text.index('static files')
    # Another document is never pulled in.
    assert 'subclass of a dictionary' not in text

    with pytest.raises(LookupError):
        docpacks.read(pack['id'], '999')
    with pytest.raises(LookupError):
        docpacks.read(pack['id'], 'not-a-number')


def test_read_window_stays_inside_the_budget(root, tmp_path, monkeypatch):
    monkeypatch.setattr(docpacks, 'MAX_ARTICLE_CHARS', 200)
    big = [('d.md', 'Doc', f'S{i}', f'section {i} ' + 'x' * 80) for i in range(10)]
    pack = upload(root, tmp_path, chunks=big)
    text = docpacks.read(pack['id'], '5')['text']
    assert 'section 4' in text or 'section 6' in text
    assert 'section 5' in text
    assert 'section 0' not in text and 'section 9' not in text


def test_many_packages_are_narrowed_to_the_ones_the_query_names(monkeypatch):
    monkeypatch.setattr(docpacks, 'MAX_PACKS_PER_SEARCH', 2)
    packs = [{'id': str(i), 'name': n, 'created_at': i}
             for i, n in enumerate(['flask', 'react', 'django', '@trpc/server'])]
    chosen = docpacks.select(packs, {'trpc', 'router'})
    assert [p['name'] for p in chosen] == ['@trpc/server', 'django']
    assert docpacks.select(packs[:2], {'anything'}) == packs[:2]


def test_federated_search_finds_docpacks_without_any_zim_library(root, tmp_path):
    upload(root, tmp_path)
    found = archive.search_many(['flask blueprint register'], limit=5)
    assert found['results']
    hit = found['results'][0]
    assert hit['archiveId'].startswith('docpack:')
    assert hit['archiveKind'] == 'docs' and hit['archiveTitle'] == 'flask 3.1.3 docs'


def test_docpack_hits_merge_with_zim_hits_and_get_their_own_share(
        root, tmp_path, monkeypatch):
    fake_library(monkeypatch, tmp_path, [
        ('wikipedia_en_all.zim', wiki(results={
            'flask blueprint': [f'Flask_{i}' for i in range(20)],
        })),
    ])
    upload(root, tmp_path)
    found = archive.search_many(['flask blueprint'], limit=8)
    ids = [h['archiveId'] for h in found['results']]
    assert any(i.startswith('docpack:') for i in ids)
    assert any(not i.startswith('docpack:') for i in ids)
    assert len(found['searched']) == 2


def test_disabled_or_kind_scoped_out_packages_are_not_searched(root, tmp_path):
    pack = upload(root, tmp_path)
    assert archive.search_many(['blueprint'], kinds_wanted=['encyclopedia'])['results'] == []
    docpacks.set_enabled(pack['id'], False)
    assert archive.search_many(['blueprint'])['results'] == []


def test_a_package_whose_file_vanished_is_skipped_not_fatal(root, tmp_path):
    pack = upload(root, tmp_path)
    (root / pack['filename']).unlink()
    found = archive.search_many(['blueprint'])
    assert found['results'] == [] and found['skipped'] == 1


def test_model_tools_search_and_read_a_docpack(root, tmp_path):
    upload(root, tmp_path)
    text, event = tools.run_tool('local_knowledge_search',
                                 {'queries': ['register blueprint', 'flask blueprints']})
    assert event['ok'] and 'flask 3.1.3 docs' in text
    archive_id = next(line.split('archiveId=')[1].split()[0]
                      for line in text.splitlines() if 'archiveId=docpack:' in line)

    text, event = tools.run_tool('local_knowledge_read', {'archiveId': archive_id, 'path': '2'})
    assert event['ok'] and 'register_blueprint' in text
    assert event['evidence']['archiveId'] == archive_id
    assert event['sources'][0]['url'] == f'/api/knowledge/archives/{archive_id}/content/2'


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

def test_routes_upload_list_toggle_read_and_delete(client, root, tmp_path):
    source = build_pack(tmp_path / 'up.db')
    with source.open('rb') as fh:
        resp = client.post('/api/knowledge/docpacks/upload',
                           data={'file': (fh, 'flask.db')},
                           content_type='multipart/form-data')
    assert resp.status_code == 201
    pack = resp.get_json()
    assert pack['id'].startswith('docpack:') and pack['available'] is True

    listed = client.get('/api/knowledge/docpacks').get_json()
    assert [p['name'] for p in listed] == ['flask']

    page = client.get(f"/api/knowledge/archives/{pack['id']}/content/2")
    assert page.status_code == 200
    assert b'register_blueprint' in page.data
    assert "script-src 'none'" in page.headers['Content-Security-Policy']
    assert client.get(f"/api/knowledge/archives/{pack['id']}/content/999").status_code == 404

    searched = client.get('/api/knowledge/search?q=blueprint').get_json()
    assert searched['results'][0]['archiveId'] == pack['id']

    toggled = client.patch(f"/api/knowledge/docpacks/{pack['id']}", json={'enabled': False})
    assert toggled.get_json()['enabled'] is False

    assert client.delete(f"/api/knowledge/docpacks/{pack['id']}").status_code == 200
    assert client.get('/api/knowledge/docpacks').get_json() == []
    assert client.delete(f"/api/knowledge/docpacks/{pack['id']}").status_code == 404


def test_routes_reject_a_bad_upload_and_report_registry_errors(client, root, monkeypatch):
    resp = client.post('/api/knowledge/docpacks/upload',
                       data={'file': (io.BytesIO(b'nope' * 100), 'x.db')},
                       content_type='multipart/form-data')
    assert resp.status_code == 400

    def offline(url, **kw):
        raise requests.ConnectionError('no route')

    monkeypatch.setattr(docpacks.requests, 'get', offline)
    assert client.get('/api/knowledge/docpacks/registry?registry=pip&name=flask').status_code == 502
    assert client.post('/api/knowledge/docpacks/install',
                       json={'registry': 'pip', 'name': 'flask', 'version': '3.1.3'}).status_code == 502
    assert client.post('/api/knowledge/docpacks/install',
                       json={'registry': 'pip', 'name': '../x', 'version': '1'}).status_code == 400


def test_schema_has_the_docpacks_table():
    cols = {r['name'] for r in get_db().execute('PRAGMA table_info(knowledge_docpacks)')}
    assert {'registry', 'name', 'version', 'filename', 'enabled', 'chunk_count'} <= cols
