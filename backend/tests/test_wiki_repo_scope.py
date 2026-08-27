"""Per-repository wiki articles and snapshots.

The wiki was built for one thing — web research about a problem space — with a
globally `UNIQUE` slug. Once several repositories each keep their own code
notes, two of them will both want `scheduling`, and one silently overwriting the
other is the failure this scoping exists to prevent.
"""
import sqlite3

import pytest

from backend.db.connection import get_db
from backend.research import repo_job, wiki

pytestmark = pytest.mark.usefixtures('isolated_db')


def _repo(repo_id: str, slug: str) -> str:
    get_db().execute(
        'INSERT INTO repos(id, slug, name, remote_url, branch, clone_state,'
        ' is_default, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (repo_id, slug, slug.title(), f'https://h/o/{slug}.git', '', 'ready', 0, 1, 1),
    )
    get_db().commit()
    return repo_id


# --- The shape the migration produced ---

def test_two_repos_can_hold_the_same_slug(client):
    _repo('r1', 'alpha')
    _repo('r2', 'beta')
    a = wiki.upsert_article('scheduling', 'Scheduling in Alpha', 's', 'alpha body',
                            repo_id='r1', kind='code')
    b = wiki.upsert_article('scheduling', 'Scheduling in Beta', 's', 'beta body',
                            repo_id='r2', kind='code')

    assert a['id'] != b['id']
    assert wiki.get_article('scheduling', 'r1')['content'] == 'alpha body'
    assert wiki.get_article('scheduling', 'r2')['content'] == 'beta body'


def test_a_repeat_slug_within_one_repo_still_revises_rather_than_duplicating(client):
    _repo('r1', 'alpha')
    first = wiki.upsert_article('sched', 'Scheduling', 's', 'v1', repo_id='r1')
    second = wiki.upsert_article('sched', 'Scheduling', 's', 'v2', repo_id='r1')

    assert first['id'] == second['id']
    assert second['revision'] == 2
    assert wiki.revisions(second['id'])[0]['content'] == 'v1'


def test_the_unique_constraint_is_enforced_by_the_database(client):
    _repo('r1', 'alpha')
    wiki.upsert_article('sched', 'Scheduling', 's', 'body', repo_id='r1')
    with pytest.raises(sqlite3.IntegrityError):
        get_db().execute(
            'INSERT INTO wiki_articles(id, repo_id, slug, title, kind,'
            ' created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
            ('dup', 'r1', 'sched', 'Dup', 'code', 1, 1),
        )


def test_an_unscoped_note_and_a_repo_note_may_share_a_slug(client):
    """NULL repo_ids are distinct to SQLite's UNIQUE, which is what keeps the
    pre-existing global research notes out of every repo's way."""
    _repo('r1', 'alpha')
    wiki.upsert_article('sched', 'How people schedule', 's', 'research', repo_id=None)
    wiki.upsert_article('sched', 'Scheduling here', 's', 'code', repo_id='r1')
    assert wiki.get_article('sched', None)['content'] == 'research'
    assert wiki.get_article('sched', 'r1')['content'] == 'code'


# --- Listing and searching ---

def test_listing_is_scoped(client):
    _repo('r1', 'alpha')
    _repo('r2', 'beta')
    wiki.upsert_article('a', 'A', '', 'x', repo_id='r1')
    wiki.upsert_article('b', 'B', '', 'x', repo_id='r2')
    wiki.upsert_article('c', 'C', '', 'x', repo_id=None)

    assert [a['slug'] for a in wiki.list_articles(repo_id='r1')] == ['a']
    assert [a['slug'] for a in wiki.list_articles(repo_id='r2')] == ['b']
    assert [a['slug'] for a in wiki.list_articles(repo_id=None)] == ['c']


def test_listing_can_be_narrowed_to_one_kind(client):
    _repo('r1', 'alpha')
    wiki.upsert_article('mod', 'A module', '', 'x', repo_id='r1', kind='code')
    wiki.upsert_article('prior', 'Prior art', '', 'x', repo_id='r1', kind='research')
    assert [a['slug'] for a in wiki.list_articles(repo_id='r1', kind='code')] == ['mod']


def test_search_is_scoped_to_a_repo(client):
    _repo('r1', 'alpha')
    _repo('r2', 'beta')
    wiki.upsert_article('sched-a', 'Scheduling', 'daemon loops', 'x', repo_id='r1')
    wiki.upsert_article('sched-b', 'Scheduling', 'daemon loops', 'x', repo_id='r2')

    assert [a['slug'] for a in wiki.search_articles('scheduling', repo_id='r1')] \
        == ['sched-a']


def test_search_overfetches_so_a_small_repo_is_not_crowded_out(client):
    """Without over-fetching, a repo with one article can come back empty
    because the top FTS hits all belonged to a busier one."""
    _repo('r1', 'alpha')
    _repo('r2', 'beta')
    for n in range(8):
        wiki.upsert_article(f'noise-{n}', f'Scheduling {n}', 'loops', 'x', repo_id='r2')
    wiki.upsert_article('mine', 'Scheduling here', 'loops', 'x', repo_id='r1')

    assert [a['slug'] for a in wiki.search_articles('scheduling', repo_id='r1')] \
        == ['mine']


# --- The tools ---

def test_a_repo_agent_sees_its_own_notes_and_the_unscoped_research(client):
    """"How do other people do this" is not about any one codebase; hiding it
    would make every repo re-research the same problem space."""
    _repo('r1', 'alpha')
    _repo('r2', 'beta')
    wiki.upsert_article('mine', 'Mine', 'about alpha', 'x', repo_id='r1', kind='code')
    wiki.upsert_article('theirs', 'Theirs', 'about beta', 'x', repo_id='r2', kind='code')
    wiki.upsert_article('prior', 'Prior art', 'general', 'x', repo_id=None)

    text, event = wiki.WikiTools('r1').run_tool('wiki_list', {})
    assert 'mine' in text and 'prior' in text
    assert 'theirs' not in text
    assert event['count'] == 2


def test_wiki_read_prefers_this_repos_article_over_the_unscoped_one(client):
    _repo('r1', 'alpha')
    wiki.upsert_article('sched', 'General', '', 'the general note', repo_id=None)
    wiki.upsert_article('sched', 'Specific', '', 'the repo note', repo_id='r1')

    text, _ = wiki.WikiTools('r1').run_tool('wiki_read', {'slug': 'sched'})
    assert 'the repo note' in text


def test_wiki_read_falls_back_to_the_unscoped_article(client):
    _repo('r1', 'alpha')
    wiki.upsert_article('sched', 'General', '', 'the general note', repo_id=None)
    text, event = wiki.WikiTools('r1').run_tool('wiki_read', {'slug': 'sched'})
    assert event['ok'] and 'the general note' in text


def test_the_unscoped_tools_see_only_unscoped_notes(client):
    _repo('r1', 'alpha')
    wiki.upsert_article('mine', 'Mine', '', 'x', repo_id='r1')
    wiki.upsert_article('prior', 'Prior art', '', 'x', repo_id=None)

    text, _ = wiki.run_tool('wiki_list', {})
    assert 'prior' in text and 'mine' not in text


def test_wiki_search_through_the_tools_merges_without_duplicating(client):
    _repo('r1', 'alpha')
    wiki.upsert_article('a', 'Scheduling here', 'loops', 'x', repo_id='r1')
    wiki.upsert_article('b', 'Scheduling generally', 'loops', 'x', repo_id=None)

    text, event = wiki.WikiTools('r1').run_tool('wiki_search', {'query': 'scheduling'})
    assert event['count'] == 2
    assert text.count('- a:') == 1 and text.count('- b:') == 1


def test_locked_still_stops_the_agent_per_repo(client):
    _repo('r1', 'alpha')
    article = wiki.upsert_article('sched', 'Scheduling', '', 'v1', repo_id='r1')
    get_db().execute('UPDATE wiki_articles SET locked=1 WHERE id=?', (article['id'],))
    get_db().commit()

    with pytest.raises(wiki.ArticleLocked):
        wiki.upsert_article('sched', 'Scheduling', '', 'v2', repo_id='r1')
    # The same slug in another repo is a different article and is untouched.
    _repo('r2', 'beta')
    assert wiki.upsert_article('sched', 'Scheduling', '', 'v2', repo_id='r2')


def test_deleting_a_repo_takes_its_articles_with_it(client):
    _repo('r1', 'alpha')
    wiki.upsert_article('mine', 'Mine', '', 'x', repo_id='r1')
    wiki.upsert_article('prior', 'Prior art', '', 'x', repo_id=None)

    get_db().execute('DELETE FROM repos WHERE id=?', ('r1',))
    get_db().commit()

    assert wiki.get_article('mine', 'r1') is None
    assert wiki.get_article('prior', None) is not None


# --- Snapshots ---

def test_snapshots_are_per_repo(client):
    _repo('r1', 'alpha')
    _repo('r2', 'beta')
    db = get_db()
    for sid, rid, sha in (('s1', 'r1', 'aaa'), ('s2', 'r2', 'bbb'), ('s3', None, 'ccc')):
        db.execute(
            'INSERT INTO repo_snapshots(id, repo_id, git_sha, generated_at, created_at)'
            ' VALUES (?,?,?,?,?)', (sid, rid, sha, 100, 100))
    db.commit()

    assert repo_job.current_snapshot('r1')['gitSha'] == 'aaa'
    assert repo_job.current_snapshot('r2')['gitSha'] == 'bbb'
    # No repo id still means the app's own checkout, as it always did.
    assert repo_job.current_snapshot()['gitSha'] == 'ccc'


def test_a_repo_with_no_snapshot_gets_none_not_someone_elses(client):
    _repo('r1', 'alpha')
    _repo('r2', 'beta')
    get_db().execute(
        'INSERT INTO repo_snapshots(id, repo_id, git_sha, generated_at, created_at)'
        ' VALUES (?,?,?,?,?)', ('s1', 'r1', 'aaa', 100, 100))
    get_db().commit()
    assert repo_job.current_snapshot('r2') is None


def test_pruning_keeps_the_newest_per_repo(client, monkeypatch):
    """Pruning globally would let a busy repo evict a quiet one's only
    snapshot, and an idea judged against nothing gets no assessment at all."""
    monkeypatch.setattr(repo_job, 'KEEP_SNAPSHOTS', 2)
    _repo('r1', 'alpha')
    _repo('r2', 'beta')
    db = get_db()
    for n in range(5):
        db.execute(
            'INSERT INTO repo_snapshots(id, repo_id, git_sha, generated_at, created_at)'
            ' VALUES (?,?,?,?,?)', (f'a{n}', 'r1', f'sha{n}', 100 + n, 1))
    db.execute(
        'INSERT INTO repo_snapshots(id, repo_id, git_sha, generated_at, created_at)'
        ' VALUES (?,?,?,?,?)', ('b0', 'r2', 'only', 1, 1))
    db.commit()

    repo_job._prune(db, 'r1')

    assert db.execute(
        "SELECT COUNT(*) c FROM repo_snapshots WHERE repo_id='r1'").fetchone()['c'] == 2
    assert db.execute(
        "SELECT COUNT(*) c FROM repo_snapshots WHERE repo_id='r2'").fetchone()['c'] == 1
