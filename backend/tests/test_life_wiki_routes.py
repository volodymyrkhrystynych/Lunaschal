"""Reading and correcting the life wiki.

These routes are what make it fair to run a pass that writes notes about the
user without asking. The wiki had no HTTP surface at all before — it only held
notes about code, read and written by agents.
"""
import pytest

from backend.db.connection import get_db
from backend.lifewiki import facts as facts_mod
from backend.research import wiki


@pytest.fixture
def article(client):
    a = wiki.upsert_article('health-and-training', 'Health and training',
                            'How they train.', 'They train three times a week.',
                            kind=wiki.LIFE_KIND)
    facts_mod.add_fact(a['id'], 'Trains three times a week.',
                       source_kind='journal', source_id='01JOURNAL')
    return a


def test_the_index_lists_life_articles_only(client, article):
    wiki.upsert_article('scheduling', 'Scheduling', '', '', kind='research')
    body = client.get('/api/life-wiki').get_json()
    assert [a['slug'] for a in body] == ['health-and-training']


def test_an_article_comes_back_with_its_facts_and_their_citations(client, article):
    """The citation is what makes drift visible rather than merely plausible —
    the user can follow it to the entry and see for themselves."""
    body = client.get('/api/life-wiki/health-and-training').get_json()
    assert body['content'] == 'They train three times a week.'
    assert body['facts'][0]['statement'] == 'Trains three times a week.'
    assert body['facts'][0]['sourceId'] == '01JOURNAL'


def test_a_research_article_is_not_reachable_through_these_routes(client):
    wiki.upsert_article('scheduling', 'Scheduling', '', '', kind='research')
    assert client.get('/api/life-wiki/scheduling').status_code == 404


def test_the_user_can_rewrite_an_article(client, article):
    resp = client.put('/api/life-wiki/health-and-training',
                      json={'content': 'In my own words.'})
    assert resp.status_code == 200
    assert resp.get_json()['content'] == 'In my own words.'


def test_an_edit_is_recorded_as_the_users(client, article):
    client.put('/api/life-wiki/health-and-training', json={'content': 'Mine.'})
    revisions = client.get('/api/life-wiki/health-and-training/revisions').get_json()

    # Found by author rather than by position: `upsert_article` numbers a
    # revision after the version it replaces, so the creation row and the first
    # edit's row share a revision number and their relative order is arbitrary.
    mine = [r for r in revisions if r['author'] == 'user']
    assert len(mine) == 1
    # Copy-on-write: the revision holds what it said *before* the edit.
    assert mine[0]['content'] == 'They train three times a week.'


def test_locking_an_article_stops_the_pass_rewriting_it(client, article):
    client.post('/api/life-wiki/health-and-training/lock', json={'locked': True})
    assert client.get('/api/life-wiki/health-and-training').get_json()['locked']

    with pytest.raises(wiki.ArticleLocked):
        wiki.upsert_article('health-and-training', 'x', '', 'overwritten',
                            kind=wiki.LIFE_KIND, author='agent')


def test_unlocking_hands_it_back(client, article):
    client.post('/api/life-wiki/health-and-training/lock', json={'locked': True})
    client.post('/api/life-wiki/health-and-training/lock', json={'locked': False})
    wiki.upsert_article('health-and-training', 'x', '', 'agent wrote this',
                        kind=wiki.LIFE_KIND, author='agent')
    assert client.get('/api/life-wiki/health-and-training').get_json()['content'] == (
        'agent wrote this'
    )


def test_locking_a_fact_freezes_it_against_the_pass(client, article):
    fact = facts_mod.live_facts(article['id'])[0]
    resp = client.post(f'/api/life-wiki/facts/{fact["id"]}/lock', json={'locked': True})
    assert resp.status_code == 200

    replacement = facts_mod.add_fact(article['id'], 'Never trains.',
                                     source_kind='journal', source_id='b')
    assert facts_mod.supersede(fact['id'], replacement) is False


def test_a_fact_can_be_deleted_outright(client, article):
    fact = facts_mod.live_facts(article['id'])[0]
    assert client.delete(f'/api/life-wiki/facts/{fact["id"]}').status_code == 200
    assert facts_mod.live_facts(article['id']) == []
    assert client.delete(f'/api/life-wiki/facts/{fact["id"]}').status_code == 404


def test_rebuild_is_accepted_and_runs_off_the_request(client, article, monkeypatch):
    """Several model calls on a local server — a synchronous request would hold
    the connection open for a minute or more."""
    called = {}

    def fake_rebuild(slug, now=None):
        called['slug'] = slug

    monkeypatch.setattr('backend.lifewiki.job.rebuild_article', fake_rebuild)
    resp = client.post('/api/life-wiki/health-and-training/rebuild')
    assert resp.status_code == 202

    from backend.routes import life_wiki as routes
    for _ in range(200):
        if called:
            break
        import time as _t
        _t.sleep(0.01)
    assert called['slug'] == 'health-and-training'


def test_rebuilding_something_that_does_not_exist_is_a_404(client):
    assert client.post('/api/life-wiki/nothing/rebuild').status_code == 404
