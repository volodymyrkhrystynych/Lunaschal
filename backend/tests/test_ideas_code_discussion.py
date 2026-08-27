"""Discussing an idea against a repository the agent can actually read.

The tab shipped with a system prompt that said "do not use them to look up
things about Lunaschal itself; the inventory below is authoritative" — correct
while the agent had no way to look anything up, and exactly backwards once it
does. These tests pin the inversion, and the rule that decides which tools a
discussion gets.
"""
import json

import pytest

from backend.db.connection import get_db
from backend.repos import registry
from backend.research import discuss


@pytest.fixture
def repos_root(monkeypatch, tmp_path):
    monkeypatch.setenv('REPOS_ROOT', str(tmp_path / 'repos'))
    monkeypatch.setenv('GRAPHIFY_BIN', str(tmp_path / 'absent'))
    return tmp_path / 'repos'


@pytest.fixture
def ready_repo(repos_root):
    """A registered repo with a checkout on disk."""
    repo = registry.create_repo('https://github.com/o/fixture.git', name='Fixture')
    root = repos_root / repo['slug']
    (root / 'backend').mkdir(parents=True)
    (root / 'backend' / 'app.py').write_text('def create_app():\n    return 1\n')
    registry.set_state(repo['id'], 'ready')
    return registry.get_repo(repo['id'])


def _make_idea(repo_id=None) -> str:
    db = get_db()
    db.execute(
        'INSERT INTO ideas(id, title, raw_content, content, status, repo_id,'
        ' created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)',
        ('idea1', 'A thing', 'raw', '', 'new', repo_id, 1, 1),
    )
    db.commit()
    return 'idea1'


# --- Which repo an idea is about ---

def test_an_idea_uses_its_own_repo(client, ready_repo):
    _make_idea(ready_repo['id'])
    assert discuss.idea_repo('idea1')['id'] == ready_repo['id']


def test_an_idea_with_no_repo_falls_back_to_the_only_one(client, ready_repo):
    """A single-repo setup should need no configuring, and ideas captured
    before repositories existed should pick up code tools without being edited."""
    _make_idea(None)
    assert discuss.idea_repo('idea1')['id'] == ready_repo['id']


def test_a_repo_that_never_finished_cloning_is_not_used(client, repos_root):
    repo = registry.create_repo('https://github.com/o/pending.git')
    _make_idea(repo['id'])
    assert discuss.idea_repo('idea1') is None


def test_with_no_repos_at_all_there_is_none(client, repos_root):
    _make_idea(None)
    assert discuss.idea_repo('idea1') is None


# --- The toolbox ---

def test_a_repo_discussion_gets_code_tools_alongside_web_and_wiki(client, ready_repo):
    tools, dispatch, code_tools = discuss.build_toolbox(ready_repo)
    names = [t['function']['name'] for t in tools]

    assert 'code_search' in names and 'read_file' in names and 'list_dir' in names
    # The three toolboxes compose; the code tools do not replace the others.
    assert 'web_search' in names and 'wiki_read' in names
    assert code_tools is not None

    # Every tool the model can see must be runnable — an unrunnable one comes
    # back as "Unknown tool", which reads as broken rather than as wrong.
    assert set(names) == set(dispatch)


def test_without_a_repo_there_are_no_code_tools_at_all(client, repos_root):
    tools, dispatch, code_tools = discuss.build_toolbox(None)
    names = [t['function']['name'] for t in tools]

    assert code_tools is None
    assert not any(n.startswith('code_') or n in ('read_file', 'list_dir') for n in names)
    assert 'web_search' in names
    assert set(names) == set(dispatch)


def test_code_map_is_offered_only_when_the_repo_has_a_graph(
    client, ready_repo, repos_root, monkeypatch, tmp_path
):
    names = [t['function']['name'] for t in discuss.build_toolbox(ready_repo)[0]]
    assert 'code_map' not in names

    fake = tmp_path / 'graphify'
    fake.write_text('#!/bin/sh\nexit 0\n')
    fake.chmod(0o755)
    monkeypatch.setenv('GRAPHIFY_BIN', str(fake))
    root = repos_root / ready_repo['slug']
    (root / 'graphify-out').mkdir()
    (root / 'graphify-out' / 'graph.json').write_text('{"nodes": []}')

    names = [t['function']['name'] for t in discuss.build_toolbox(ready_repo)[0]]
    assert 'code_map' in names


def test_a_repo_row_whose_checkout_vanished_gets_no_code_tools(client, ready_repo, repos_root):
    """The row says ready; the directory is gone. Offering read_file here would
    fail on every call."""
    import shutil
    shutil.rmtree(repos_root / ready_repo['slug'])
    assert discuss.build_toolbox(ready_repo)[2] is None


def test_the_code_tools_are_fresh_per_discussion(client, ready_repo):
    """The read budget is per-run state. A shared instance would let an earlier
    discussion exhaust a later one."""
    first = discuss.build_toolbox(ready_repo)[2]
    first.read_file('backend/app.py')
    assert first.reads == 1
    assert discuss.build_toolbox(ready_repo)[2].reads == 0


# --- The prompt ---

def test_the_prompt_tells_a_code_agent_to_check_and_cite(client):
    prompt = discuss.system_prompt(has_repo=True, repo_name='Fixture')
    assert 'Fixture' in prompt
    assert 'path/to/file.py:123' in prompt
    assert 'must come from a file you opened' in prompt.replace('\n', ' ')
    # The instruction that used to be there, and was the whole problem.
    assert 'authoritative' not in prompt


def test_the_prompt_does_not_promise_a_map_it_does_not_have(client):
    assert 'code map' not in discuss.system_prompt(has_repo=True)
    assert 'code map' in discuss.system_prompt(has_repo=True, has_map=True)


def test_without_a_repo_the_prompt_says_it_cannot_verify(client):
    prompt = discuss.system_prompt(has_repo=False)
    assert 'cannot verify' in prompt
    assert 'read_file' not in prompt


def test_the_inventory_is_labelled_an_index_not_the_truth(client, monkeypatch):
    monkeypatch.setattr(
        'backend.research.discuss.current_snapshot',
        lambda: {'digest': '## Routes\nGET /api/things'},
    )
    _make_idea(None)
    context = discuss.build_context('idea1')
    assert 'verify against the source' in context
    assert 'authoritative' not in context


def test_the_answer_instruction_asks_for_file_citations(client):
    assert 'path:line' in discuss.ANSWER_INSTRUCTION


# --- End to end through the route ---

def test_the_discussion_stream_records_files_read_as_sources(
    client, ready_repo, monkeypatch
):
    """Provenance from what happened, not from what the model claims — the same
    rule agent._loop already applies to web_fetch."""
    _make_idea(ready_repo['id'])
    get_db().execute(
        'INSERT INTO conversations(id, title, idea_id, created_at, updated_at)'
        ' VALUES (?,?,?,?,?)', ('c1', 'Chat', 'idea1', 1, 1))
    get_db().commit()

    captured = {}

    def fake_gather(system, request_text, **kwargs):
        captured['system'] = system
        captured['tools'] = [t['function']['name'] for t in kwargs['tools']]
        captured['max_turns'] = kwargs.get('max_turns')
        step = kwargs['dispatch']['read_file'].run_tool(
            'read_file', {'path': 'backend/app.py'})[1]
        yield ('step', step)
        yield ('result', {'messages': [], 'steps': [step], 'sources': []})

    monkeypatch.setattr('backend.research.agent.gather_events', fake_gather)
    monkeypatch.setattr('backend.ai.llm.chat_stream_deltas', lambda *a, **k: iter(['ok']))
    monkeypatch.setattr('backend.routes.ideas.is_ai_configured', lambda: True)

    resp = client.post('/api/ideas/idea1/discuss',
                       json={'conversationId': 'c1', 'message': 'how does this work?'})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    assert 'read_file' in captured['tools']
    assert captured['max_turns'] == discuss.CODE_MAX_TURNS
    assert 'Fixture' in captured['system']

    done = [json.loads(line[6:]) for line in body.splitlines()
            if line.startswith('data: {') and '"done"' in line]
    assert done and done[0]['sources'] == [{'file': 'backend/app.py', 'line': 1}]

    stored = get_db().execute(
        "SELECT metadata FROM messages WHERE role='assistant'").fetchone()
    assert json.loads(stored['metadata'])['sources'] == [
        {'file': 'backend/app.py', 'line': 1}
    ]


# --- Capture ---

def test_a_new_idea_is_stamped_with_the_default_repo(client, ready_repo):
    resp = client.post('/api/ideas', json={'rawContent': 'an idea'})
    idea_id = resp.get_json()['id']
    row = get_db().execute('SELECT repo_id FROM ideas WHERE id=?', (idea_id,)).fetchone()
    assert row['repo_id'] == ready_repo['id']


def test_an_explicit_repo_wins_over_the_default(client, ready_repo, repos_root):
    other = registry.create_repo('https://github.com/o/other.git')
    registry.set_state(other['id'], 'ready')
    resp = client.post('/api/ideas', json={'rawContent': 'x', 'repoId': other['id']})
    row = get_db().execute(
        'SELECT repo_id FROM ideas WHERE id=?', (resp.get_json()['id'],)).fetchone()
    assert row['repo_id'] == other['id']


def test_with_no_repos_capture_still_works(client, repos_root):
    resp = client.post('/api/ideas', json={'rawContent': 'a plain product thought'})
    assert resp.status_code == 201
    row = get_db().execute(
        'SELECT repo_id FROM ideas WHERE id=?', (resp.get_json()['id'],)).fetchone()
    assert row['repo_id'] is None


def test_an_idea_can_be_moved_between_repos_and_detached(client, ready_repo):
    _make_idea(ready_repo['id'])
    client.patch('/api/ideas/idea1', json={'repoId': ''})
    assert get_db().execute(
        'SELECT repo_id FROM ideas WHERE id=?', ('idea1',)).fetchone()['repo_id'] is None

    client.patch('/api/ideas/idea1', json={'repoId': ready_repo['id']})
    assert get_db().execute(
        'SELECT repo_id FROM ideas WHERE id=?', ('idea1',)
    ).fetchone()['repo_id'] == ready_repo['id']
