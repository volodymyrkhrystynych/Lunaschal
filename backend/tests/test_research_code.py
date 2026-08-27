"""The code toolbox: what the agent may read, and what it may not.

Two groups of tests matter here. The refusals — a clone is someone else's code
and may contain any symlink or dotfile it likes, and an article that quotes a
credential leaks it into every prompt that article is ever retrieved into. And
the budgets — a code pass reads far more than a web pass fetches, so the ceiling
has to actually hold.
"""
import pytest

from backend.research import code

pytestmark = pytest.mark.usefixtures('isolated_db')


@pytest.fixture
def repo(tmp_path):
    """A small checkout to read."""
    root = tmp_path / 'repo'
    (root / 'backend').mkdir(parents=True)
    (root / 'src').mkdir()
    (root / '.git').mkdir()
    (root / 'node_modules' / 'left-pad').mkdir(parents=True)

    (root / 'backend' / 'app.py').write_text(
        'from flask import Flask\n\n\ndef create_app():\n'
        '    app = Flask(__name__)\n    return app\n'
    )
    (root / 'backend' / 'worker.py').write_text(
        '\n'.join(f'line {n}' for n in range(1, 501)) + '\n'
    )
    (root / 'src' / 'App.tsx').write_text('export function App() {\n  return null;\n}\n')
    (root / 'README.md').write_text('# Fixture repo\n')
    (root / '.env').write_text('SECRET_KEY=hunter2\n')
    (root / '.git' / 'config').write_text('[remote "origin"]\n  url = git@h:o/r.git\n')
    (root / 'node_modules' / 'left-pad' / 'index.js').write_text('// create_app noise\n')
    (root / 'logo.png').write_bytes(b'\x89PNG\r\n\x1a\n' + b'0' * 100)
    return root


@pytest.fixture
def tools(repo):
    return code.CodeTools(repo)


# --- code_search ---

def test_search_finds_a_definition_and_cites_a_relative_path(tools):
    text, event = tools.code_search('def create_app')
    assert event['ok'] and event['count'] == 1
    # Repo-relative, always: this is what a plan cites and what a coding agent
    # will go looking for. An absolute path from this machine is useless there.
    assert text.startswith('backend/app.py:4:')
    assert str(tools.root) not in text


def test_search_skips_dependencies_and_build_output(tools):
    """node_modules contains the same string; a match from it is noise."""
    text, event = tools.code_search('create_app')
    assert 'node_modules' not in text
    assert event['count'] == 1


def test_search_never_reads_out_of_a_dotenv(tools):
    """resolve_within refuses .env by path, but rg walks the tree itself and
    would otherwise print the line before anyone asked to read the file."""
    # Search for the key, not the value: a "no matches" reply quotes the query
    # back, so asking for the secret itself cannot distinguish a leak from an
    # echo. What must never appear is the value sitting next to the key.
    text, event = tools.code_search('SECRET_KEY')
    assert event['count'] == 0
    assert 'hunter2' not in text


def test_search_with_no_matches_is_a_result_not_a_failure(tools):
    """rg exits 1 for no matches. Reporting that as a broken tool would send
    the model looking for a different tool instead of a different query."""
    text, event = tools.code_search('definitely_not_in_this_repo')
    assert event['ok'] is True
    assert event['count'] == 0
    assert 'No matches' in text


def test_search_honours_a_glob(tools):
    _, event = tools.code_search('App', glob='*.tsx')
    assert event['count'] == 1


def test_search_in_a_subdirectory(tools):
    _, event = tools.code_search('Flask', path='backend')
    assert event['ok'] and event['count'] >= 1


def test_search_outside_the_repo_is_refused(tools):
    _, event = tools.code_search('anything', path='../..')
    assert event['ok'] is False
    assert event['error'] == 'bad path'


def test_search_caps_its_matches(repo, monkeypatch):
    """rg's own --max-count is per file, so the overall cap has to be ours."""
    monkeypatch.setattr(code, 'MAX_SEARCH_MATCHES', 3)
    for f in range(5):
        (repo / f'many{f}.py').write_text(
            '\n'.join(f'needle_{f}_{n} = {n}' for n in range(10))
        )
    text, event = code.CodeTools(repo).code_search('needle_')
    assert event['count'] == 3
    assert 'more matches not shown' in text


def test_an_empty_query_is_refused_rather_than_matching_everything(tools):
    _, event = tools.code_search('   ')
    assert event['ok'] is False


# --- read_file ---

def test_read_returns_numbered_lines_and_records_the_file(tools):
    text, event = tools.read_file('backend/app.py')
    assert event['ok'] and event['file'] == 'backend/app.py'
    assert '1\tfrom flask import Flask' in text
    assert tools.files_read == ['backend/app.py']


def test_read_a_window_of_a_long_file(tools):
    text, event = tools.read_file('backend/worker.py', 100, 104)
    assert '100\tline 100' in text and '104\tline 104' in text
    assert 'line 105' not in text
    assert 'lines 100-104 of 500' in text


def test_a_long_file_says_how_to_get_the_rest(tools):
    """A model that does not know more exists writes its note from the first
    page and calls it done."""
    text, _ = tools.read_file('backend/worker.py')
    assert 'more lines' in text
    assert 'start=401' in text


def test_reading_the_same_file_twice_records_it_once(tools):
    tools.read_file('backend/app.py')
    tools.read_file('backend/app.py')
    assert tools.files_read == ['backend/app.py']
    assert tools.reads == 2


@pytest.mark.parametrize('path', [
    '../../../etc/passwd', '..', '/etc/passwd', 'backend/../../outside',
])
def test_read_outside_the_repo_is_refused(tools, path):
    text, event = tools.read_file(path)
    assert event['ok'] is False
    assert 'Refusing' in text or 'No such file' in text


@pytest.mark.parametrize('path', ['.env', '.git/config'])
def test_read_of_a_credential_or_git_internal_is_refused(tools, path):
    text, event = tools.read_file(path)
    assert event['ok'] is False
    assert 'hunter2' not in text and 'git@h:o/r.git' not in text


def test_read_of_a_symlink_pointing_out_of_the_repo_is_refused(repo, tmp_path):
    outside = tmp_path / 'secrets.txt'
    outside.write_text('do not leak me')
    (repo / 'link.txt').symlink_to(outside)
    text, event = code.CodeTools(repo).read_file('link.txt')
    assert event['ok'] is False
    assert 'do not leak me' not in text


def test_read_of_a_binary_says_so_instead_of_returning_mojibake(tools):
    text, event = tools.read_file('logo.png')
    assert event['ok'] is False
    assert 'binary' in text


def test_read_of_a_missing_file_is_an_ordinary_result(tools):
    text, event = tools.read_file('backend/nope.py')
    assert event['ok'] is False
    assert 'No such file' in text


def test_the_read_budget_holds(repo):
    tools = code.CodeTools(repo, max_reads=2)
    assert tools.read_file('backend/app.py')[1]['ok'] is True
    assert tools.read_file('src/App.tsx')[1]['ok'] is True

    text, event = tools.read_file('README.md')
    assert event['ok'] is False
    assert event['error'] == 'read budget exhausted'
    # The message tells the model what to do next, not just that it failed.
    assert 'Work with what you have' in text


def test_a_refused_read_does_not_spend_budget(repo):
    """Otherwise a model probing paths could burn the whole budget on nothing."""
    tools = code.CodeTools(repo, max_reads=2)
    tools.read_file('../../etc/passwd')
    tools.read_file('.env')
    tools.read_file('nope.py')
    assert tools.reads == 0
    assert tools.read_file('backend/app.py')[1]['ok'] is True


# --- list_dir ---

def test_list_dir_at_the_root_hides_dependencies_and_secrets(tools):
    text, event = tools.list_dir()
    assert event['ok']
    assert 'backend/' in text and 'src/' in text and 'README.md' in text
    assert 'node_modules' not in text
    assert '.env' not in text


def test_list_dir_of_a_subdirectory(tools):
    text, _ = tools.list_dir('backend')
    assert 'app.py' in text and 'worker.py' in text


def test_list_dir_outside_the_repo_is_refused(tools):
    _, event = tools.list_dir('../..')
    assert event['ok'] is False


# --- the toolbox itself ---

def test_code_map_is_absent_without_a_graph(repo, monkeypatch, tmp_path):
    """A tool that always answers 'unavailable' costs a turn to learn nothing."""
    monkeypatch.setenv('GRAPHIFY_BIN', str(tmp_path / 'absent'))
    assert code.tool_names(repo) == ['code_search', 'read_file', 'list_dir']


def test_code_map_appears_once_a_graph_exists(repo, monkeypatch, tmp_path):
    fake = tmp_path / 'graphify'
    fake.write_text('#!/bin/sh\nexit 0\n')
    fake.chmod(0o755)
    monkeypatch.setenv('GRAPHIFY_BIN', str(fake))
    (repo / 'graphify-out').mkdir()
    (repo / 'graphify-out' / 'graph.json').write_text('{"nodes": []}')
    assert 'code_map' in code.tool_names(repo)


def test_dispatch_covers_exactly_the_tools_offered(repo, monkeypatch, tmp_path):
    """A tool the model can see but the dispatch cannot run comes back as
    'Unknown tool', which reads as a broken tool rather than a wrong choice."""
    monkeypatch.setenv('GRAPHIFY_BIN', str(tmp_path / 'absent'))
    tools = code.CodeTools(repo)
    dispatch = code.dispatch_for(tools, repo)
    assert set(dispatch) == set(code.tool_names(repo))
    assert all(handler is tools for handler in dispatch.values())


def test_run_tool_routes_every_offered_tool(tools):
    assert tools.run_tool('code_search', {'query': 'Flask'})[1]['ok'] is True
    assert tools.run_tool('read_file', {'path': 'README.md'})[1]['ok'] is True
    assert tools.run_tool('list_dir', {})[1]['ok'] is True
    assert tools.run_tool('nonsense', {})[1]['error'] == 'unknown tool'


def test_run_tool_never_raises(tools, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError('exploded')

    monkeypatch.setattr(tools, 'code_search', boom)
    text, event = tools.run_tool('code_search', {'query': 'x'})
    assert event['ok'] is False
    assert 'exploded' in text


def test_code_map_without_a_graph_suggests_searching(repo, monkeypatch, tmp_path):
    monkeypatch.setenv('GRAPHIFY_BIN', str(tmp_path / 'absent'))
    text, event = code.CodeTools(repo).code_map('anything')
    assert event['count'] == 0
    assert 'code_search' in text


# --- provenance ---

def test_files_read_comes_from_the_steps_not_from_the_model(tools):
    """The same rule agent._loop applies to web_fetch: a source is what was
    actually opened."""
    steps = [
        tools.read_file('backend/app.py')[1],
        tools.code_search('Flask')[1],
        tools.read_file('backend/app.py')[1],
        tools.read_file('missing.py')[1],
        {'tool': 'web_fetch', 'ok': True, 'url': 'https://example.com'},
    ]
    assert code.files_read(steps) == [{'file': 'backend/app.py', 'line': 1}]


def test_files_read_of_nothing_is_empty():
    assert code.files_read([]) == []
    assert code.files_read(None) == []
