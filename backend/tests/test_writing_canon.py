"""Canon sources: importing a MediaWiki dump, and the project-scoped tools
that search it.

The fixture is a tiny Fandom-shaped export — siteinfo, an article with an
infobox and sections, a redirect, and a talk page — written as plain XML and
gzipped, the two shapes that need no optional dependency to read.
"""
import gzip
import time

import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.delegate import chat as delegate_chat
from backend.offline_knowledge import docpacks
from backend.writing import canon_import, discuss
from backend.writing.canon_tools import CanonTools

pytestmark = pytest.mark.usefixtures('client')

DUMP = """<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/" version="0.11">
  <siteinfo>
    <sitename>Salt Roads Wiki</sitename>
    <base>https://saltroads.fandom.com/wiki/Main_Page</base>
  </siteinfo>
  <page>
    <title>Mara Vell</title>
    <ns>0</ns>
    <id>1</id>
    <revision><id>10</id><text xml:space="preserve">{{Infobox character
|name = Mara Vell
|affiliation = The Harbour Guild
|age = 19
|image = Mara.png
}}
'''Mara Vell''' is a smuggler from [[Port Ash]].&lt;ref&gt;Book 1&lt;/ref&gt;

== Early life ==
Mara grew up on the docks and learned to [[Swimming|swim]] before she could read.
[[Category:Characters]]
[[File:Docks.jpg|thumb|The docks]]

== Abilities ==
She can pick any lock in Port Ash.
</text></revision>
  </page>
  <page>
    <title>Mara</title>
    <ns>0</ns>
    <redirect title="Mara Vell" />
    <revision><text>#REDIRECT [[Mara Vell]]</text></revision>
  </page>
  <page>
    <title>Talk:Mara Vell</title>
    <ns>1</ns>
    <revision><text>Is she really 19? secretword</text></revision>
  </page>
</mediawiki>
"""


@pytest.fixture
def canon_root(monkeypatch, tmp_path):
    root = tmp_path / 'canon'
    monkeypatch.setenv('CANON_ROOT', str(root))
    return root


def _dump(tmp_path, text=DUMP, gz=False):
    path = tmp_path / ('dump.xml.gz' if gz else 'dump.xml')
    data = text.encode()
    path.write_bytes(gzip.compress(data) if gz else data)
    return path


def _project(title='The Salt Roads'):
    now = int(time.time())
    pid = str(ULID())
    get_db().execute(
        'INSERT INTO writing_projects(id, title, created_at, updated_at) VALUES (?,?,?,?)',
        (pid, title, now, now))
    get_db().commit()
    return pid


def _source(tmp_path, *, gz=False):
    now = int(time.time())
    sid = str(ULID())
    get_db().execute(
        "INSERT INTO writing_canon_sources(id, name, status, created_at, updated_at)"
        " VALUES (?, '', 'importing', ?, ?)", (sid, now, now))
    get_db().commit()
    canon_import.run(sid, path=_dump(tmp_path, gz=gz))
    return sid


def _link(pid, sid):
    get_db().execute('INSERT INTO writing_project_canon VALUES (?,?,?)',
                     (pid, sid, int(time.time())))
    get_db().commit()


# --- wikitext ---------------------------------------------------------------

def test_infobox_facts_survive_and_markup_does_not():
    text = canon_import.wikitext_to_text(
        "{{Infobox character|name=Mara|affiliation=[[The Harbour Guild]]|age=19|image=x.png}}"
        "'''Mara''' is from [[Port Ash|the port]].<ref>cite</ref>[[Category:People]]")
    assert 'affiliation: The Harbour Guild' in text
    assert 'age: 19' in text
    assert 'x.png' not in text and 'cite' not in text and 'Category' not in text
    assert 'Mara is from the port.' in text


def test_sections_split_at_level_two_headings():
    sections = canon_import.split_sections('lead\n== A ==\none\n=== A1 ===\ndeeper\n== B ==\ntwo')
    assert [h for h, _ in sections] == ['', 'A', 'B']
    assert 'A1' in sections[1][1] and 'deeper' in sections[1][1]


# --- importing --------------------------------------------------------------

@pytest.mark.parametrize('gz', [False, True])
def test_a_dump_imports_articles_and_aliases_but_not_talk_pages(canon_root, tmp_path, gz):
    sid = _source(tmp_path, gz=gz)

    row = get_db().execute('SELECT * FROM writing_canon_sources WHERE id=?', (sid,)).fetchone()
    assert row['status'] == 'ready', row['error']
    assert (row['pages'], row['name'], row['wiki_host']) == (1, 'Salt Roads Wiki', 'saltroads.fandom.com')
    assert (canon_root / row['filename']).is_file()
    # Nothing left behind but the finished file.
    assert [p.name for p in canon_root.iterdir()] == [row['filename']]


def test_a_doctype_is_refused(canon_root, tmp_path):
    now = int(time.time())
    sid = str(ULID())
    get_db().execute("INSERT INTO writing_canon_sources(id, name, status, created_at, updated_at)"
                     " VALUES (?, 'x', 'importing', ?, ?)", (sid, now, now))
    get_db().commit()
    evil = '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><mediawiki>&lol;</mediawiki>'
    canon_import.run(sid, path=_dump(tmp_path, evil))

    row = get_db().execute('SELECT status, error FROM writing_canon_sources WHERE id=?', (sid,)).fetchone()
    assert row['status'] == 'error' and 'DOCTYPE' in row['error']


def test_something_that_is_not_a_wiki_export_is_refused(canon_root, tmp_path):
    with pytest.raises(canon_import.CanonImportError, match='MediaWiki'):
        canon_import.build('x', _dump(tmp_path, '<html><body>hi</body></html>'))


# --- searching --------------------------------------------------------------

def test_a_linked_project_can_search_and_read_its_canon(canon_root, tmp_path):
    pid = _project()
    _link(pid, _source(tmp_path))
    tools = CanonTools(pid)

    text, event = tools.run_tool('canon_search', {'query': 'lock'})
    assert event['count'] >= 1 and 'Mara Vell' in text
    ref = text.split('[', 1)[1].split(']', 1)[0]

    page, event = tools.run_tool('canon_read', {'id': ref})
    assert event['ok'] and 'pick any lock' in page
    assert 'saltroads.fandom.com' in page and 'CC BY-SA' in page


def test_the_short_name_finds_the_real_article(canon_root, tmp_path):
    pid = _project()
    _link(pid, _source(tmp_path))
    text, _ = CanonTools(pid).run_tool('canon_search', {'query': 'Mara'})
    assert 'another name for Mara Vell' in text or 'Mara Vell' in text


def test_talk_pages_are_not_canon(canon_root, tmp_path):
    pid = _project()
    _link(pid, _source(tmp_path))
    _text, event = CanonTools(pid).run_tool('canon_search', {'query': 'secretword'})
    assert event['count'] == 0


def test_an_unlinked_project_sees_no_canon(canon_root, tmp_path):
    _link(_project('Linked'), _source(tmp_path))
    other = _project('Other')
    assert not CanonTools(other).available
    assert not CanonTools(None).available
    _text, event = CanonTools(other).run_tool('canon_search', {'query': 'lock'})
    assert event['ok'] is False


def test_canon_never_reaches_the_global_library(canon_root, tmp_path):
    _link(_project(), _source(tmp_path))
    assert docpacks.rows() == []


def test_a_forged_id_reads_nothing(canon_root, tmp_path):
    pid = _project()
    sid = _source(tmp_path)
    _link(pid, sid)
    _text, event = CanonTools(_project('Other')).run_tool('canon_read', {'id': f'{sid}:1'})
    assert event['ok'] is False


def test_linking_canon_adds_the_tools_and_the_prompt_line(canon_root, tmp_path):
    pid = _project()
    tools, _dispatch, _note = delegate_chat._toolbox(
        'writing', conversation_id=None, checkpoint=None, deadline=None, writing_project_id=pid)
    assert 'canon_search' not in {t['function']['name'] for t in tools}
    assert 'canon_search' not in discuss.system_prompt(pid)

    _link(pid, _source(tmp_path))

    tools, dispatch, note = delegate_chat._toolbox(
        'writing', conversation_id=None, checkpoint=None, deadline=None, writing_project_id=pid)
    assert {'canon_search', 'canon_read'} <= set(dispatch)
    assert 'canon_search' in note and 'canon_search' in discuss.system_prompt(pid)


# --- routes -----------------------------------------------------------------

def test_upload_link_and_delete_through_the_routes(client, canon_root, tmp_path):
    pid = _project()
    with open(_dump(tmp_path, gz=True), 'rb') as fh:
        resp = client.post('/api/writing/canon', data={'file': (fh, 'dump.xml.gz'), 'name': 'Salt'},
                           content_type='multipart/form-data')
    assert resp.status_code == 202
    sid = resp.get_json()['id']
    assert canon_import.wait_idle()

    listed = client.get('/api/writing/canon').get_json()
    assert listed[0]['status'] == 'ready' and listed[0]['name'] == 'Salt'
    assert 'filename' not in listed[0]

    assert client.put(f'/api/writing/projects/{pid}/canon', json={'sourceIds': [sid]}).status_code == 200
    assert client.get(f'/api/writing/projects/{pid}/canon').get_json() == {'sourceIds': [sid]}
    assert client.put(f'/api/writing/projects/{pid}/canon',
                      json={'sourceIds': ['nope']}).status_code == 400

    assert client.delete(f'/api/writing/canon/{sid}').status_code == 200
    assert list(canon_root.glob('*.sqlite')) == []
    assert client.get(f'/api/writing/projects/{pid}/canon').get_json() == {'sourceIds': []}


def test_fetching_a_private_address_is_refused(client, canon_root):
    resp = client.post('/api/writing/canon/fetch', json={'url': 'http://127.0.0.1/dump.xml.gz'})
    assert resp.status_code == 202
    assert canon_import.wait_idle()
    row = client.get('/api/writing/canon').get_json()[0]
    assert row['status'] == 'error'


def test_a_wiki_url_resolves_to_its_statistics_dump(monkeypatch):
    html = ('<a href="https://s3.amazonaws.com/wikia_xml_dumps/s/sa/'
            'saltroads_pages_current.xml.7z">Current pages</a>')

    class Resp:
        text = html

        def close(self):
            pass

    seen = {}

    def fake_get(url, stream=False):
        seen['url'] = url
        return Resp(), url

    monkeypatch.setattr(canon_import, '_get', fake_get)
    assert canon_import.resolve_dump_url('https://saltroads.fandom.com/wiki/Mara_Vell') == (
        'https://s3.amazonaws.com/wikia_xml_dumps/s/sa/saltroads_pages_current.xml.7z')
    assert seen['url'] == 'https://saltroads.fandom.com/wiki/Special:Statistics'
    assert canon_import.resolve_dump_url('https://x.org/a.xml.gz') == 'https://x.org/a.xml.gz'


def test_a_7z_dump_as_fandom_serves_it_imports(canon_root, tmp_path):
    py7zr = pytest.importorskip('py7zr')
    xml = _dump(tmp_path)
    packed = tmp_path / 'saltroads_pages_current.xml.7z'
    with py7zr.SevenZipFile(packed, 'w') as archive:
        archive.write(xml, 'saltroads_pages_current.xml')
    result = canon_import.build('s7', packed)
    assert result['pages'] == 1 and (canon_root / 's7.sqlite').is_file()
