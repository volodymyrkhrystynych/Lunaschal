"""Blanking a search provider nothing implements any more.

Tavily was removed with no migration, and the stored name outlived the code: it
falls off `web.web_search`'s if-chain, so `is_search_configured()` answers False
and every web search in the app is dead — while Settings' <select> has no
matching <option> and renders blank, which reads as "None", i.e. as a setting
the user chose. The failure is silent at both ends, which is why this is a
migration rather than a note in the release.
"""
from backend.db.connection import (
    get_db, _clear_retired_search_provider, _migrate_websearch_search_to_research,
)


def _set(db, **cols):
    assignments = ', '.join(f'{k}=?' for k in cols)
    db.execute(f'UPDATE settings SET {assignments}', tuple(cols.values()))
    db.commit()


def _provider(db, column='research_search_provider'):
    return db.execute(f'SELECT {column} FROM settings LIMIT 1').fetchone()[column]


def test_a_retired_provider_is_blanked(client):
    db = get_db()
    _set(db, research_search_provider='tavily', research_search_key='sk-old')

    _clear_retired_search_provider(db)

    assert _provider(db) == ''
    # The key is left alone: blanking the provider is enough to make the panel
    # honest, and a key the user re-selects should still be there.
    assert db.execute(
        'SELECT research_search_key FROM settings LIMIT 1'
    ).fetchone()['research_search_key'] == 'sk-old'


def test_running_it_twice_changes_nothing(client):
    db = get_db()
    _set(db, research_search_provider='tavily')

    _clear_retired_search_provider(db)
    _clear_retired_search_provider(db)

    assert _provider(db) == ''


def test_a_provider_that_still_exists_is_untouched(client):
    db = get_db()
    for provider in ('brave', 'searxng', ''):
        _set(db, research_search_provider=provider)
        _clear_retired_search_provider(db)
        assert _provider(db) == provider


def test_a_null_provider_is_left_null(client):
    """NULL is "never configured", which is already the truth."""
    db = get_db()
    _set(db, research_search_provider=None)

    _clear_retired_search_provider(db)

    assert _provider(db) is None


def test_the_old_tabs_column_is_cleaned_too(client):
    """Otherwise the fold below would carry the dead name forward."""
    db = get_db()
    _set(db, websearch_search_provider='tavily')

    _clear_retired_search_provider(db)

    assert _provider(db, 'websearch_search_provider') == ''


def test_blanking_first_lets_the_websearch_fold_rescue_a_working_key(client):
    """Order matters: the fold only fills a *blank* research provider, so a row
    stuck on a retired name would keep a working Brave key unreachable forever."""
    db = get_db()
    _set(db, research_search_provider='tavily', research_search_key=None,
         websearch_search_provider='brave', websearch_search_key='sk-test')

    _clear_retired_search_provider(db)
    _migrate_websearch_search_to_research(db)

    row = db.execute(
        'SELECT research_search_provider, research_search_key FROM settings LIMIT 1'
    ).fetchone()
    assert row['research_search_provider'] == 'brave'
    assert row['research_search_key'] == 'sk-test'


def test_the_app_agrees_it_is_unconfigured_afterwards(client):
    """The point of the blank: what Settings shows and what the app does are
    the same claim again."""
    from backend.research import web

    db = get_db()
    _set(db, research_search_provider='tavily', research_search_key='sk-old')
    _clear_retired_search_provider(db)

    assert web.search_provider() == ''
    assert web.is_search_configured() is False


def test_init_db_runs_it(client):
    """It has to fire on the real startup path, not only when called by hand."""
    from backend.db.connection import init_db

    db = get_db()
    _set(db, research_search_provider='tavily')
    init_db()

    assert _provider(db) == ''
