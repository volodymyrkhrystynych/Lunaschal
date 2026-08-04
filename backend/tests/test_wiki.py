"""The LLM wiki: copy-on-write revisions, FTS retrieval, and the model tools."""
import json

import pytest

from backend.db.connection import get_db
from backend.research import wiki


def _write(slug='spaced-repetition', title='Spaced repetition', summary='How FSRS schedules.',
           content='FSRS models memory with difficulty, stability and retrievability.', **kw):
    return wiki.upsert_article(slug, title, summary, content, **kw)


def test_slugify():
    assert wiki.slugify('Spaced Repetition!') == 'spaced-repetition'
    assert wiki.slugify('  FSRS vs SM-2  ') == 'fsrs-vs-sm-2'
    assert wiki.slugify('///') == 'untitled'
    assert len(wiki.slugify('x' * 200)) <= 80


def test_create_then_read(client):
    article = _write()
    assert article['slug'] == 'spaced-repetition'
    assert article['revision'] == 1
    assert wiki.get_article('spaced-repetition')['title'] == 'Spaced repetition'
    assert wiki.get_article('missing') is None


def test_creation_logs_a_first_revision(client):
    article = _write()
    history = wiki.revisions(article['id'])
    assert len(history) == 1
    assert history[0]['revision'] == 1
    assert history[0]['note'] == 'created'


def test_editing_is_copy_on_write(client):
    """The revision row holds what the article *used* to say, so the history
    reads as a trail rather than duplicating the current row."""
    first = _write(content='Old body.')
    second = _write(content='New body.', note='refreshed from a new source')

    assert second['revision'] == 2
    assert second['content'] == 'New body.'

    history = wiki.revisions(first['id'])
    assert [r['revision'] for r in history] == [1, 1]  # created + the replaced copy
    replaced = [r for r in history if r['note'] == 'refreshed from a new source'][0]
    assert replaced['content'] == 'Old body.'
    assert '-Old body.' in replaced['diff']
    assert '+New body.' in replaced['diff']


def test_a_locked_article_rejects_the_agent_but_not_the_user(client):
    article = _write()
    get_db().execute('UPDATE wiki_articles SET locked=1 WHERE id=?', (article['id'],))
    get_db().commit()

    with pytest.raises(wiki.ArticleLocked):
        _write(content='agent overwrite')
    assert wiki.get_article('spaced-repetition')['content'].startswith('FSRS models')

    _write(content='user edit', author='user')
    assert wiki.get_article('spaced-repetition')['content'] == 'user edit'


def test_sources_and_tags_are_stored(client):
    article = _write(
        sources=[{'title': 'FSRS paper', 'url': 'https://ex.com/f'}],
        tags=['  Learning ', 'learning', 'Scheduling'],
    )
    assert json.loads(article['sources'])[0]['url'] == 'https://ex.com/f'
    assert json.loads(article['tags']) == ['learning', 'scheduling']


def test_content_is_clipped_to_the_context_budget(client):
    article = _write(content='x' * 50_000, summary='y' * 5_000)
    assert len(article['content']) == wiki.MAX_ARTICLE_CHARS
    assert len(article['summary']) == wiki.MAX_SUMMARY_CHARS


def test_list_articles_is_newest_first(client):
    _write(slug='a', title='A')
    _write(slug='b', title='B')
    get_db().execute("UPDATE wiki_articles SET updated_at=1 WHERE slug='a'")
    get_db().commit()
    assert [a['slug'] for a in wiki.list_articles()] == ['b', 'a']


# --- FTS ---

def test_fts_finds_a_word_only_in_the_body(client):
    _write(slug='fsrs', title='Scheduling', summary='Overview.',
           content='The retrievability curve is exponential.')
    hits = wiki.search_articles('retrievability')
    assert [h['slug'] for h in hits] == ['fsrs']


def test_fts_ranks_a_title_match_above_a_body_mention(client):
    _write(slug='mentions-anki', title='Scheduling theory', summary='General.',
           content='Anki is mentioned here once in passing.')
    _write(slug='anki', title='Anki', summary='The app.', content='Deck options and card types.')
    assert [h['slug'] for h in wiki.search_articles('anki')][0] == 'anki'


def test_fts_index_follows_edits_and_deletes(client):
    article = _write(content='original vocabulary')
    assert wiki.search_articles('vocabulary')

    _write(content='completely different words')
    assert wiki.search_articles('vocabulary') == []
    assert wiki.search_articles('completely')

    get_db().execute('DELETE FROM wiki_articles WHERE id=?', (article['id'],))
    get_db().commit()
    assert wiki.search_articles('completely') == []


def test_fts_ignores_an_empty_or_punctuation_query(client):
    _write()
    assert wiki.search_articles('   ') == []
    assert wiki.search_articles('!!!') == []


# --- Idea links ---

def test_linking_an_idea_and_listing_its_articles(client):
    idea_id = client.post('/api/ideas', json={'title': 'Anki parity'}).get_json()['id']
    a = _write(slug='anki', title='Anki', summary='The app.')
    b = _write(slug='fsrs', title='FSRS', summary='The scheduler.')

    wiki.link_idea(idea_id, a['id'], relevance=0.4)
    wiki.link_idea(idea_id, b['id'], relevance=0.9)

    linked = wiki.articles_for_idea(idea_id)
    assert [x['slug'] for x in linked] == ['fsrs', 'anki']


def test_relinking_updates_rather_than_duplicating(client):
    idea_id = client.post('/api/ideas', json={'title': 'x'}).get_json()['id']
    article = _write()
    wiki.link_idea(idea_id, article['id'], relevance=0.2)
    wiki.link_idea(idea_id, article['id'], relevance=0.8)
    assert len(wiki.articles_for_idea(idea_id)) == 1


def test_deleting_an_article_cascades_links_and_revisions(client):
    idea_id = client.post('/api/ideas', json={'title': 'x'}).get_json()['id']
    article = _write()
    wiki.link_idea(idea_id, article['id'])

    get_db().execute('DELETE FROM wiki_articles WHERE id=?', (article['id'],))
    get_db().commit()

    assert wiki.articles_for_idea(idea_id) == []
    assert wiki.revisions(article['id']) == []


def test_deleting_an_idea_cascades_its_links(client):
    idea_id = client.post('/api/ideas', json={'title': 'x'}).get_json()['id']
    article = _write()
    wiki.link_idea(idea_id, article['id'])

    client.delete(f'/api/ideas/{idea_id}')
    left = get_db().execute('SELECT COUNT(*) AS n FROM idea_wiki_links').fetchone()['n']
    assert left == 0


# --- Tools ---

def test_wiki_list_tool_returns_the_whole_index(client):
    _write(slug='a', title='A', summary='First.')
    _write(slug='b', title='B', summary='Second.')
    text, event = wiki.run_tool('wiki_list', {})
    assert '- a: A — First.' in text
    assert event['count'] == 2


def test_wiki_list_tool_on_an_empty_wiki(client):
    text, event = wiki.run_tool('wiki_list', {})
    assert 'empty' in text.lower()
    assert event['count'] == 0


def test_wiki_search_tool(client):
    _write(slug='fsrs', title='FSRS', summary='Scheduler.', content='stability')
    text, event = wiki.run_tool('wiki_search', {'query': 'stability'})
    assert 'fsrs' in text
    assert event['ok'] is True

    text, event = wiki.run_tool('wiki_search', {'query': 'nothingmatchesthis'})
    assert 'No wiki articles match' in text
    assert event['count'] == 0


def test_wiki_read_tool(client):
    _write()
    text, event = wiki.run_tool('wiki_read', {'slug': 'spaced-repetition'})
    assert text.startswith('# Spaced repetition')
    assert event['ok'] is True

    text, event = wiki.run_tool('wiki_read', {'slug': 'nope'})
    assert 'No wiki article' in text
    assert event['ok'] is False


def test_unknown_wiki_tool_is_reported_not_raised(client):
    text, event = wiki.run_tool('wiki_delete_everything', {})
    assert 'Unknown tool' in text
    assert event['ok'] is False


def test_tool_definitions_are_openai_shaped():
    assert {t['function']['name'] for t in wiki.TOOLS} == {
        'wiki_list', 'wiki_search', 'wiki_read'
    }
