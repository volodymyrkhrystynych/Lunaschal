"""Folding the retired Web Search tab's search provider into the research one.

Searching is now something the chat delegate decides to do, through the same
backend/research/web.py the Ideas agent uses — so there is one provider for the
whole app and one place in Settings to set it. Someone who only ever configured
search under the old Web Search tab would otherwise find the delegate unable to
search, with a working API key sitting in a column nothing reads any more.
"""
from backend.db.connection import get_db, _migrate_websearch_search_to_research


def _set(db, **cols):
    assignments = ', '.join(f'{k}=?' for k in cols)
    db.execute(f'UPDATE settings SET {assignments}', tuple(cols.values()))
    db.commit()


def _research(db):
    row = db.execute(
        'SELECT research_search_provider, research_search_key,'
        ' research_searxng_url FROM settings LIMIT 1'
    ).fetchone()
    return dict(row)


def test_a_websearch_only_config_is_carried_over(client):
    db = get_db()
    _set(db, research_search_provider='', research_search_key=None,
         websearch_search_provider='brave', websearch_search_key='sk-test')

    _migrate_websearch_search_to_research(db)

    assert _research(db)['research_search_provider'] == 'brave'
    assert _research(db)['research_search_key'] == 'sk-test'


def test_a_searxng_url_comes_across_too(client):
    db = get_db()
    _set(db, research_search_provider='', research_searxng_url=None,
         websearch_search_provider='searxng',
         websearch_searxng_url='http://localhost:8888')

    _migrate_websearch_search_to_research(db)

    assert _research(db)['research_searxng_url'] == 'http://localhost:8888'


def test_a_deliberate_research_config_is_never_overwritten(client):
    """Someone who configured both chose the research one on purpose."""
    db = get_db()
    _set(db, research_search_provider='tavily', research_search_key='keep-me',
         websearch_search_provider='brave', websearch_search_key='sk-other')

    _migrate_websearch_search_to_research(db)

    assert _research(db)['research_search_provider'] == 'tavily'
    assert _research(db)['research_search_key'] == 'keep-me'


def test_nothing_happens_when_neither_was_configured(client):
    db = get_db()
    _set(db, research_search_provider='', websearch_search_provider='')

    _migrate_websearch_search_to_research(db)

    assert _research(db)['research_search_provider'] == ''


def test_the_migration_is_idempotent(client):
    """Runs on every startup with no version flag: after the copy the research
    provider is non-empty, so the guard never fires again."""
    db = get_db()
    _set(db, research_search_provider='', websearch_search_provider='brave',
         websearch_search_key='sk-test')

    _migrate_websearch_search_to_research(db)
    _set(db, research_search_key='user-changed-it')
    _migrate_websearch_search_to_research(db)

    assert _research(db)['research_search_key'] == 'user-changed-it'


def test_the_delegate_reads_the_migrated_provider(client):
    """The point of the migration: web.py must now see it."""
    from backend.research import web

    db = get_db()
    _set(db, research_search_provider='', websearch_search_provider='brave',
         websearch_search_key='sk-test')
    _migrate_websearch_search_to_research(db)

    assert web.search_provider() == 'brave'
    assert web.is_search_configured() is True
