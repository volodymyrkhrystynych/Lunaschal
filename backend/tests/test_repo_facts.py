"""The deterministic repo-context extractors.

Most of these run against the real working tree on purpose: they assert facts
that are true of this repo, so they double as drift detectors. The parsing
edge cases use a fixture tree instead, where the input can be made pathological.
"""
import textwrap

import pytest

from backend.db.connection import get_db
from backend.research import repo_facts as rf


@pytest.fixture
def root():
    return rf.repo_root()


# --- Against the real tree ---

def test_repo_root_is_this_checkout(root):
    assert rf.is_repo(root)
    assert (root / 'backend' / 'routes' / 'ideas.py').is_file()


def test_is_repo_rejects_an_unrelated_directory(tmp_path):
    """The guard that stops a misconfigured root crawling the filesystem."""
    assert not rf.is_repo(tmp_path)


def test_route_facts_finds_known_routes(root):
    routes = rf.route_facts(root)
    paths = {(r['method'], r['path']) for r in routes}
    assert ('POST', '/api/chat/stream') in paths
    assert ('GET', '/api/ideas') in paths
    assert ('POST', '/api/ideas/voice') in paths
    # url_prefix is applied, not just the rule.
    assert all(r['path'].startswith(('/', '{prefix}')) for r in routes)
    # Every route knows where it came from.
    assert all(r['file'].startswith('backend/routes/') for r in routes)


def test_route_facts_mark_a_factory_blueprints_prefix_as_dynamic(root):
    """files.py builds a blueprint whose prefix is a parameter, so it is only
    knowable at the call site — better to say so than to emit a bare rule that
    reads like a real, mountable path."""
    files_routes = [r for r in rf.route_facts(root) if r['file'].endswith('routes/files.py')]
    assert files_routes
    assert all(r['path'].startswith('{prefix}') for r in files_routes)


def test_route_facts_captures_the_docstring_summary(root):
    routes = {r['function']: r for r in rf.route_facts(root)}
    assert routes['list_paper_pages']['doc'].startswith('Flat feed')


def test_table_facts_reads_the_live_db_including_migrations(client):
    """PRAGMA over the live connection, so `_ensure_*` columns are included —
    a static parse of schema.sql would miss every migrated column."""
    tables = {t['table']: t for t in rf.table_facts(get_db())}
    assert 'ideas' in tables
    assert 'learning_cards' in tables

    cols = {c['name'] for c in tables['learning_cards']['columns']}
    assert 'fsrs_state' in cols
    # briefing_hour only ever existed as an ALTER TABLE in connection.py.
    settings_cols = {c['name'] for c in tables['settings']['columns']}
    assert 'briefing_hour' in settings_cols


def test_table_facts_flags_fts_virtual_tables(client):
    tables = {t['table']: t for t in rf.table_facts(get_db())}
    assert tables['journal_fts']['virtual'] is True
    assert tables['journal_entries']['virtual'] is False


def test_view_facts_agree_across_the_three_hand_synced_lists(root):
    facts = rf.view_facts(root)
    assert 'ideas' in facts['views']
    assert {n['view'] for n in facts['navItems']} == set(facts['views'])
    assert [n['view'] for n in facts['navItems']] == facts['viewOrder']
    assert facts['warnings'] == []


def test_api_facts_finds_namespaces(root):
    namespaces = {ns['namespace']: ns['methods'] for ns in rf.api_facts(root)}
    assert 'ideas' in namespaces
    assert 'createFromVoice' in namespaces['ideas']
    assert 'writing' in namespaces


def test_ai_facts_summarize_each_module(root):
    modules = {m['module']: m['purpose'] for m in rf.ai_facts(root)}
    assert 'llm' in modules
    assert modules['images'].startswith('Image captioning')


def test_doc_facts_index_the_human_docs_verbatim(root):
    docs = {d['path']: d for d in rf.doc_facts(root)}
    headings = [h['text'] for h in docs['docs/ROADMAP.md']['headings']]
    assert 'Backups (mandatory)' in headings
    # Roadmap bullets are kept verbatim: the assessor compares an idea against
    # what is written down, not a paraphrase of it.
    assert any('Local backup' in item for item in docs['docs/ROADMAP.md']['items'])
    assert 'CLAUDE.md' in docs


def test_settings_facts_lists_columns(client):
    cols = rf.settings_facts(get_db())
    assert 'llama_url' in cols
    assert 'briefing_enabled' in cols


def test_component_facts_skip_tests(root):
    files = {c['file'] for c in rf.component_facts(root)}
    assert 'src/components/Ideas/IdeaList.tsx' in files
    assert not any('.test.' in f for f in files)


def test_git_facts_report_head(root):
    git = rf.git_facts(root)
    assert git['sha'] and len(git['sha']) == 40
    assert git['branch']


def test_git_facts_survive_a_stale_since_sha(root):
    """A snapshot taken on a since-rebased branch must not blank the log."""
    git = rf.git_facts(root, since_sha='0' * 40)
    assert git['commits']


def test_git_facts_outside_a_repo(tmp_path):
    git = rf.git_facts(tmp_path)
    assert git['sha'] is None
    assert git['commits'] == []


# --- Parsing edge cases, on a fixture tree ---

def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding='utf-8')


def test_route_facts_handle_prefixes_methods_and_broken_files(tmp_path):
    _write(tmp_path, 'backend/routes/good.py', """
        from flask import Blueprint
        bp = Blueprint('good', __name__, url_prefix='/api/good')

        @bp.get('/items')
        def list_items():
            '''List the items.

            More detail that should not be captured.
            '''

        @bp.route('/legacy', methods=['POST', 'PUT'])
        def legacy():
            pass

        @bp.post('/items/<item_id>/act')
        def act(item_id):
            pass

        @some_other.get('/ignored')
        def ignored():
            pass
    """)
    _write(tmp_path, 'backend/routes/broken.py', 'def oops(:\n')

    routes = rf.route_facts(tmp_path)
    got = {(r['method'], r['path']) for r in routes}
    assert ('GET', '/api/good/items') in got
    assert ('POST', '/api/good/legacy') in got
    assert ('PUT', '/api/good/legacy') in got
    assert ('POST', '/api/good/items/<item_id>/act') in got
    # A decorator on something that isn't a known blueprint is not a route.
    assert not any(p == '/ignored' for _, p in got)
    # Only the docstring's first line.
    doc = next(r['doc'] for r in routes if r['function'] == 'list_items')
    assert doc == 'List the items.'


def test_route_facts_survive_one_unparseable_file(tmp_path):
    """A syntax error in one route file must not lose the whole inventory."""
    _write(tmp_path, 'backend/routes/broken.py', 'def oops(:\n')
    _write(tmp_path, 'backend/routes/fine.py', """
        from flask import Blueprint
        bp = Blueprint('fine', __name__, url_prefix='/api/fine')

        @bp.get('/x')
        def x():
            pass
    """)
    assert ('GET', '/api/fine/x') in {
        (r['method'], r['path']) for r in rf.route_facts(tmp_path)
    }


def test_view_facts_warn_when_the_lists_drift(tmp_path):
    """The four hand-synced registration points drift silently; a view missing
    from VIEW_ORDER simply can't be reached by the keyboard."""
    _write(tmp_path, 'src/lib/viewPersistence.ts', """
        export const VIEWS = ['chat', 'ideas', 'journal'] as const;
    """)
    _write(tmp_path, 'src/components/Sidebar.tsx', """
        export const navItems: { view: View; label: string; icon: string }[] = [
          { view: 'chat', label: 'Chat', icon: 'a' },
          { view: 'journal', label: 'Journal', icon: 'b' },
        ];
    """)
    _write(tmp_path, 'src/shortcuts/ShortcutProvider.tsx', """
        export const VIEW_ORDER: AppView[] = [
          'journal',
          'chat',
        ];
    """)
    facts = rf.view_facts(tmp_path)
    assert any('ideas' in w for w in facts['warnings'])
    assert any('same order' in w for w in facts['warnings'])


def test_view_facts_on_a_missing_frontend(tmp_path):
    facts = rf.view_facts(tmp_path)
    assert facts == {'views': [], 'navItems': [], 'viewOrder': [], 'warnings': []}


# --- Composition and rendering ---

def test_build_facts_composes_every_section(client, root):
    facts = rf.build_facts(root, get_db())
    for key in ('git', 'routes', 'tables', 'views', 'api', 'components', 'ai',
                'settings', 'docs'):
        assert key in facts, key
    assert facts['routes'] and facts['tables']


def test_render_digest_is_pure_markdown(client, root):
    digest = rf.render_digest(rf.build_facts(root, get_db()), 'Lunaschal')
    assert digest.startswith('# Lunaschal repo inventory')
    assert 'POST /api/ideas/voice' in digest
    assert '**ideas**' in digest
    assert '`api.ideas`' in digest
    # FTS shadow tables are noise in an inventory.
    assert '**journal_fts**' not in digest
    # The roadmap ledger is what makes "already on the list?" answerable.
    assert 'Already written down in docs/ROADMAP.md' in digest


def test_render_digest_surfaces_view_warnings():
    digest = rf.render_digest({'views': {'warnings': ['VIEWS and Sidebar navItems disagree: x']}})
    assert 'Inconsistency' in digest


def test_render_digest_handles_an_empty_snapshot():
    # Unnamed is a real case now — a repo row with a blank name — and must not
    # be a crash or an empty string.
    assert rf.render_digest({}).startswith('# Repo inventory')
    assert rf.render_digest({}, 'Lunaschal').startswith('# Lunaschal repo inventory')
