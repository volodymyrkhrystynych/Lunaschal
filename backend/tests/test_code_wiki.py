"""The nightly pass that reads a repository and writes down what it found.

`plan_modules` holds the whole policy and reads only the DB and the snapshot's
module index, so it is testable without threads or a model — the same shape as
research_job.plan_next, for the same reason.
"""
import json

import pytest

from backend.db.connection import get_db
from backend.research import code_wiki, wiki

pytestmark = pytest.mark.usefixtures('isolated_db')


def _repo(repo_id='r1', slug='alpha') -> dict:
    get_db().execute(
        'INSERT INTO repos(id, slug, name, remote_url, branch, clone_state,'
        ' is_default, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (repo_id, slug, slug.title(), f'https://h/o/{slug}.git', '', 'ready', 0, 1, 1),
    )
    get_db().commit()
    return {'id': repo_id, 'slug': slug, 'name': slug.title(), 'cloneState': 'ready'}


def _snapshot(repo_id: str, modules: list[dict], sha='sha1', at=100, sid='s1') -> None:
    get_db().execute(
        'INSERT INTO repo_snapshots(id, repo_id, git_sha, facts, generated_at,'
        ' created_at) VALUES (?,?,?,?,?,?)',
        (sid, repo_id, sha, json.dumps({'modules': modules}), at, at),
    )
    get_db().commit()


def _module(path: str, lines: int = 500, files: int = 5) -> dict:
    return {'path': path, 'files': files, 'lines': lines, 'languages': ['Python']}


# --- Slugs ---

def test_the_slug_comes_from_the_path_not_the_title(client):
    """A refresh must land on the same article. A retitled note is fine; a
    second article about the same directory is not."""
    assert code_wiki.module_slug('backend/ai') == 'backend-ai'
    assert code_wiki.module_slug('src/components/Settings') == 'src-components-settings'
    assert code_wiki.module_slug('') == 'repository-root'


# --- Planning ---

def test_nothing_is_planned_without_a_snapshot(client):
    _repo()
    assert code_wiki.plan_modules('r1') == []


def test_new_modules_are_written_largest_first(client):
    """The snapshot already orders them; the plan must not reshuffle."""
    _repo()
    _snapshot('r1', [_module('backend', 5000), _module('src', 3000),
                     _module('ops', 900)])
    assert [t['path'] for t in code_wiki.plan_modules('r1')] == \
        ['backend', 'src', 'ops']
    assert all(t['reason'] == 'new' for t in code_wiki.plan_modules('r1'))


def test_a_module_already_written_up_is_not_rewritten(client):
    _repo()
    _snapshot('r1', [_module('backend', 5000), _module('src', 3000)])
    wiki.upsert_article('backend', 'Backend', 's', 'body', repo_id='r1', kind='code')

    assert [t['path'] for t in code_wiki.plan_modules('r1')] == ['src']


def test_tiny_modules_are_not_worth_a_model_call(client):
    _repo()
    _snapshot('r1', [_module('backend', 5000), _module('scripts', 10)])
    assert [t['path'] for t in code_wiki.plan_modules('r1')] == ['backend']


def test_the_nightly_cap_holds(client):
    _repo()
    _snapshot('r1', [_module(f'mod{n}', 1000 - n) for n in range(20)])
    assert len(code_wiki.plan_modules('r1', limit=3)) == 3


def test_changed_modules_come_before_new_ones(client, monkeypatch):
    """A note that no longer describes the code is worse than a missing one:
    it will be retrieved and believed."""
    _repo()
    _snapshot('r1', [_module('huge-and-new', 9000), _module('backend/ai', 500)])
    wiki.upsert_article('backend-ai', 'AI', 's', 'body', repo_id='r1', kind='code')
    monkeypatch.setattr(code_wiki, '_changed_modules',
                        lambda root, sha: {'backend/ai'})

    plan = code_wiki.plan_modules('r1', since_sha='old')
    assert [t['path'] for t in plan] == ['backend/ai', 'huge-and-new']
    assert plan[0]['reason'] == 'changed'
    assert plan[0]['existing']['content'] == 'body'
    assert plan[1]['reason'] == 'new'


def test_the_stalest_note_is_refreshed_first(client, monkeypatch):
    _repo()
    _snapshot('r1', [_module('a', 900), _module('b', 800)])
    wiki.upsert_article('a', 'A', 's', 'x', repo_id='r1', kind='code', now=5000)
    wiki.upsert_article('b', 'B', 's', 'x', repo_id='r1', kind='code', now=1000)
    monkeypatch.setattr(code_wiki, '_changed_modules', lambda root, sha: {'a', 'b'})

    assert [t['path'] for t in code_wiki.plan_modules('r1', since_sha='old')] == \
        ['b', 'a']


def test_an_unusable_diff_range_refreshes_nothing_but_still_fills_in(client, monkeypatch):
    """A rebase makes the old sha unreachable. "We cannot tell what changed"
    reads correctly as "refresh nothing on that basis"."""
    _repo()
    _snapshot('r1', [_module('a', 900), _module('b', 800)])
    wiki.upsert_article('a', 'A', 's', 'x', repo_id='r1', kind='code')
    monkeypatch.setattr(code_wiki, '_changed_modules', lambda root, sha: set())

    plan = code_wiki.plan_modules('r1', since_sha='gone')
    assert [t['path'] for t in plan] == ['b']


def test_planning_is_scoped_to_one_repo(client):
    _repo('r1', 'alpha')
    _repo('r2', 'beta')
    _snapshot('r1', [_module('shared', 900)], sid='s1')
    _snapshot('r2', [_module('shared', 900)], sid='s2', sha='sha2')
    # Beta has written this module up; alpha has not, and must not be skipped
    # because of it.
    wiki.upsert_article('shared', 'Shared', 's', 'x', repo_id='r2', kind='code')

    assert [t['path'] for t in code_wiki.plan_modules('r1')] == ['shared']
    assert code_wiki.plan_modules('r2') == []


def test_a_research_article_does_not_count_as_a_module_note(client):
    """kind is what separates them; a web note about `scheduling` must not
    make the scheduling *module* look documented."""
    _repo()
    _snapshot('r1', [_module('backend', 900)])
    wiki.upsert_article('backend', 'Backend patterns', 's', 'x',
                        repo_id='r1', kind='research')
    assert [t['path'] for t in code_wiki.plan_modules('r1')] == ['backend']


# --- The previous sha ---

def test_the_previous_sha_is_the_one_before_the_current_snapshot(client):
    """"Changed" means "since we last looked", not "in the last commit"."""
    _repo()
    _snapshot('r1', [], sha='old', at=100, sid='s1')
    _snapshot('r1', [], sha='new', at=200, sid='s2')
    assert code_wiki._previous_sha('r1') == 'old'


def test_the_first_ever_snapshot_has_no_previous_sha(client):
    _repo()
    _snapshot('r1', [], sha='only')
    assert code_wiki._previous_sha('r1') is None


# --- Writing ---

def test_a_module_article_is_written_and_scoped(client, monkeypatch, tmp_path):
    repo = _repo()
    monkeypatch.setenv('REPOS_ROOT', str(tmp_path))
    root = tmp_path / 'alpha'
    (root / 'backend').mkdir(parents=True)
    (root / 'backend' / 'app.py').write_text('def create_app():\n    return 1\n')

    monkeypatch.setattr(
        'backend.research.agent.gather',
        lambda *a, **k: {'messages': [{'role': 'tool', 'content': 'file text'}],
                         'steps': [{'tool': 'read_file', 'ok': True,
                                    'file': 'backend/app.py', 'line': 1}]},
    )
    monkeypatch.setattr(
        'backend.ai.code_wiki.write_article',
        lambda *a, **k: {'title': 'The backend', 'summary': 'Flask app',
                         'content': 'Creates the app at backend/app.py:1.',
                         'note': 'first pass'},
    )

    article = code_wiki.write_module_article(
        repo, {'path': 'backend', 'reason': 'new', 'info': _module('backend')},
        checkpoint=lambda: None)

    assert article['slug'] == 'backend'
    assert article['repoId'] == 'r1'
    assert article['kind'] == 'code'
    # Provenance from what was opened, not from what the model says it read.
    assert json.loads(article['sources']) == [
        {'file': 'backend/app.py', 'line': 1}]


def test_a_model_that_saw_too_little_writes_nothing(client, monkeypatch, tmp_path):
    """None is a real outcome. An invented note is worse than no note, because
    it will be retrieved later and believed."""
    repo = _repo()
    monkeypatch.setenv('REPOS_ROOT', str(tmp_path))
    (tmp_path / 'alpha').mkdir()
    monkeypatch.setattr('backend.research.agent.gather',
                        lambda *a, **k: {'messages': [], 'steps': []})
    monkeypatch.setattr('backend.ai.code_wiki.write_article', lambda *a, **k: None)

    assert code_wiki.write_module_article(
        repo, {'path': 'backend', 'reason': 'new'}, checkpoint=lambda: None) is None
    assert wiki.get_article('backend', 'r1') is None


def test_a_locked_article_is_left_alone(client, monkeypatch, tmp_path):
    repo = _repo()
    monkeypatch.setenv('REPOS_ROOT', str(tmp_path))
    (tmp_path / 'alpha').mkdir()
    article = wiki.upsert_article('backend', 'Mine', 's', 'my words',
                                  repo_id='r1', kind='code')
    get_db().execute('UPDATE wiki_articles SET locked=1 WHERE id=?', (article['id'],))
    get_db().commit()

    monkeypatch.setattr('backend.research.agent.gather',
                        lambda *a, **k: {'messages': [{'role': 'tool', 'content': 'x'}],
                                         'steps': []})
    monkeypatch.setattr('backend.ai.code_wiki.write_article',
                        lambda *a, **k: {'title': 'T', 'summary': 's', 'content': 'new'})

    assert code_wiki.write_module_article(
        repo, {'path': 'backend', 'reason': 'changed'}, checkpoint=lambda: None) is None
    assert wiki.get_article('backend', 'r1')['content'] == 'my words'


def test_one_failing_module_does_not_cost_the_rest_of_the_run(client, monkeypatch):
    _repo()
    _snapshot('r1', [_module('a', 900), _module('b', 800), _module('c', 700)])

    calls = []

    def flaky(repo, target, **kwargs):
        calls.append(target['path'])
        if target['path'] == 'b':
            raise RuntimeError('boom')
        return {'slug': target['path']}

    monkeypatch.setattr(code_wiki, 'write_module_article', flaky)
    monkeypatch.setattr('backend.research.agent.make_checkpoint',
                        lambda **k: (lambda: None))

    result = code_wiki.run_code_wiki('r1')
    assert calls == ['a', 'b', 'c']
    assert result['written'] == ['a', 'c']
    assert result['skipped'] == 1


def test_cancellation_stops_the_run_rather_than_being_swallowed(client, monkeypatch):
    from backend.research.agent import Cancelled
    _repo()
    _snapshot('r1', [_module('a', 900), _module('b', 800)])

    def cancelled(repo, target, **kwargs):
        raise Cancelled('stop')

    monkeypatch.setattr(code_wiki, 'write_module_article', cancelled)
    monkeypatch.setattr('backend.research.agent.make_checkpoint',
                        lambda **k: (lambda: None))
    with pytest.raises(Cancelled):
        code_wiki.run_code_wiki('r1')


def test_a_repo_that_is_not_ready_is_skipped(client):
    _repo()
    get_db().execute("UPDATE repos SET clone_state='error' WHERE id='r1'")
    get_db().commit()
    assert code_wiki.run_code_wiki('r1') == {'written': [], 'skipped': 0}


# --- The setting ---

def test_the_nightly_count_is_configurable_and_zero_disables_it(client, monkeypatch):
    assert code_wiki.articles_per_night() == code_wiki.DEFAULT_ARTICLES_PER_NIGHT

    get_db().execute('UPDATE settings SET code_wiki_articles=2')
    get_db().commit()
    assert code_wiki.articles_per_night() == 2

    _repo()
    _snapshot('r1', [_module('a', 900)])
    get_db().execute('UPDATE settings SET code_wiki_articles=0')
    get_db().commit()

    called = []
    monkeypatch.setattr(code_wiki, 'write_module_article',
                        lambda *a, **k: called.append(1))
    assert code_wiki.run_code_wiki('r1') == {'written': [], 'skipped': 0}
    assert called == []
