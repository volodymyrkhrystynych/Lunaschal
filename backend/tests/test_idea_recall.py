"""An Ideas discussion reaching the rest of its repository's backlog.

The scope rule is the part worth pinning: it is the `repo_id` column, strictly,
and a repo-less idea is its own sealed world. Both differ from how the wiki
scopes the same-shaped data, so a future reader reaching for `WikiTools`'
union — or for `discuss.idea_repo()`'s default-repo fallback — should fail here
rather than in production.
"""
import time

import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.research.idea_recall import TOOL_NAMES, TOOLS, IdeaTools

pytestmark = pytest.mark.usefixtures('client')


def _repo(repo_id, slug, is_default=0):
    now = int(time.time())
    get_db().execute(
        'INSERT INTO repos(id, slug, name, remote_url, clone_state, is_default,'
        ' created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)',
        (repo_id, slug, slug, f'https://example.com/{slug}.git', 'ready',
         is_default, now, now),
    )
    get_db().commit()


def _idea(repo_id=None, title='', raw='', content='', status='new',
          user_verdict=None, updated=None):
    now = updated or int(time.time())
    iid = str(ULID())
    get_db().execute(
        'INSERT INTO ideas(id, title, raw_content, content, status, repo_id,'
        ' user_verdict, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (iid, title, raw, content, status, repo_id, user_verdict, now, now),
    )
    get_db().commit()
    return iid


def _assess(idea_id, verdict='yes', rationale='Already shipped in routes/x.py.'):
    now = int(time.time())
    get_db().execute(
        'INSERT INTO idea_assessments(id, idea_id, verdict, confidence,'
        ' rationale, evidence, assessed_at, created_at) VALUES (?,?,?,?,?,?,?,?)',
        (str(ULID()), idea_id, verdict, 0.8, rationale, '[]', now, now),
    )
    get_db().commit()


# --- scope ------------------------------------------------------------------

def test_a_discussion_sees_only_its_own_repositorys_ideas():
    _repo('r1', 'lunaschal')
    _repo('r2', 'other-app')
    here = _idea('r1', title='Scoped recall tools')
    _idea('r1', title='Nightly repo pass')
    mine_only = _idea('r2', title='Something for the other app')

    text, event = IdeaTools.for_idea(here).run_tool('idea_list', {})
    assert 'Nightly repo pass' in text
    assert 'Something for the other app' not in text
    assert event['count'] == 1

    text, event = IdeaTools.for_idea(mine_only).run_tool('idea_list', {})
    assert 'Nightly repo pass' not in text
    assert event['count'] == 0


def test_a_repo_less_idea_is_its_own_sealed_world():
    """The opposite of the wiki's union, deliberately: an idea with no repo is
    a plain product thought, and the other product thoughts are its world."""
    _repo('r1', 'lunaschal')
    _idea('r1', title='A code idea')
    here = _idea(None, title='A plain product thought')
    _idea(None, title='Another plain product thought')

    text, event = IdeaTools.for_idea(here).run_tool('idea_list', {})
    assert 'Another plain product thought' in text
    assert 'A code idea' not in text
    assert event['count'] == 1


def test_a_repo_idea_does_not_see_the_repo_less_ones():
    _repo('r1', 'lunaschal')
    here = _idea('r1', title='A code idea')
    _idea(None, title='A plain product thought')

    text, event = IdeaTools.for_idea(here).run_tool('idea_list', {})
    assert 'plain product thought' not in text
    assert event['count'] == 0


def test_the_default_repo_fallback_is_not_used_for_scope():
    """`discuss.idea_repo()` resolves a repo-less idea to the default repo so
    it still gets code tools. Reusing that as a recall predicate would make a
    repo-less idea see the whole database in a single-repo setup."""
    _repo('r1', 'lunaschal', is_default=1)
    _idea('r1', title='A code idea')
    here = _idea(None, title='A plain product thought')
    _idea(None, title='Another plain product thought')

    text, event = IdeaTools.for_idea(here).run_tool('idea_list', {})
    assert event['count'] == 1
    assert 'A code idea' not in text


def test_an_idea_that_does_not_exist_mounts_nothing():
    assert IdeaTools.for_idea('01NOPE') is None


# --- the idea being discussed -----------------------------------------------

def test_the_idea_being_discussed_is_excluded_everywhere():
    """It is already in the prompt verbatim, at a larger cap than this module's
    — returning a shorter copy of it would be worse than not returning it."""
    _repo('r1', 'lunaschal')
    here = _idea('r1', title='Scoped recall tools',
                 content='Give every discussion a recall tool.')
    tools = IdeaTools.for_idea(here)

    text, event = tools.run_tool('idea_list', {})
    assert 'Scoped recall tools' not in text
    assert event['count'] == 0

    _text, event = tools.run_tool('idea_search', {'query': 'recall'})
    assert event['count'] == 0

    _text, event = tools.run_tool('idea_read', {'title': 'Scoped recall tools'})
    assert event['ok'] is False
    assert event['error'] == 'not found'


# --- the index --------------------------------------------------------------

def test_a_dictated_idea_is_named_from_its_transcript():
    """Voice capture leaves `title` empty and nothing fills it in, so the list
    has to derive a name the same way the frontend does."""
    _repo('r1', 'lunaschal')
    here = _idea('r1', title='Anchor')
    _idea('r1', raw='So I was thinking we could let the chat read the journal\nand more')

    text, _event = IdeaTools.for_idea(here).run_tool('idea_list', {})
    assert 'So I was thinking we could let the chat read the journal' in text


def test_an_already_built_idea_says_so():
    _repo('r1', 'lunaschal')
    here = _idea('r1', title='Anchor')
    shipped = _idea('r1', title='Nightly repo pass', status='shipped')
    _assess(shipped, verdict='yes')

    text, _event = IdeaTools.for_idea(here).run_tool('idea_list', {})
    assert 'already built' in text


def test_the_owners_verdict_beats_the_models():
    _repo('r1', 'lunaschal')
    here = _idea('r1', title='Anchor')
    disputed = _idea('r1', title='Voice capture', user_verdict='no')
    _assess(disputed, verdict='yes')

    text, _event = IdeaTools.for_idea(here).run_tool('idea_list', {})
    assert 'already built' not in text


def test_shipped_and_parked_ideas_are_listed_too():
    """A shipped sibling is more useful than a new one: it is the answer to
    "have I already done this"."""
    _repo('r1', 'lunaschal')
    here = _idea('r1', title='Anchor')
    _idea('r1', title='Old and shipped', status='shipped')
    _idea('r1', title='Parked for now', status='parked')

    text, event = IdeaTools.for_idea(here).run_tool('idea_list', {})
    assert 'Old and shipped' in text and 'Parked for now' in text
    assert event['count'] == 2


def test_an_empty_backlog_says_so():
    _repo('r1', 'lunaschal')
    here = _idea('r1', title='The only idea')

    text, event = IdeaTools.for_idea(here).run_tool('idea_list', {})
    assert 'No other ideas' in text
    assert event['ok'] is True


# --- reading ----------------------------------------------------------------

def test_reading_an_idea_carries_its_text_and_its_assessment():
    _repo('r1', 'lunaschal')
    here = _idea('r1', title='Anchor')
    other = _idea('r1', title='Nightly repo pass', status='ready',
                  content='Walk each repo at 3am and write module notes.')
    _assess(other, verdict='partial', rationale='Half of it is in repo_job.py.')

    text, event = IdeaTools.for_idea(here).run_tool(
        'idea_read', {'title': 'nightly repo pass'})

    assert event['ok'] is True
    assert event['title'] == 'Nightly repo pass'
    assert 'Walk each repo at 3am' in text
    assert 'partly built already' in text
    assert 'Half of it is in repo_job.py.' in text
    assert 'Status: ready' in text


def test_reading_an_idea_names_its_research_notes_as_leads():
    from backend.research import wiki

    _repo('r1', 'lunaschal')
    here = _idea('r1', title='Anchor')
    other = _idea('r1', title='Spaced repetition', content='FSRS everywhere.')
    article = wiki.upsert_article('fsrs', 'FSRS', 'How FSRS schedules.', 'Body.')
    get_db().execute(
        'INSERT INTO idea_wiki_links(idea_id, article_id, relevance, created_at)'
        ' VALUES (?,?,?,?)',
        (other, article['id'], 1.0, int(time.time())),
    )
    get_db().commit()

    text, _event = IdeaTools.for_idea(here).run_tool(
        'idea_read', {'title': 'Spaced repetition'})

    assert 'fsrs (FSRS)' in text
    assert 'wiki_read' in text
    assert 'Body.' not in text          # a lead, not the article itself


def test_a_plan_is_named_but_never_pasted():
    _repo('r1', 'lunaschal')
    here = _idea('r1', title='Anchor')
    other = _idea('r1', title='Torrent tab', content='qBittorrent behind gluetun.')
    now = int(time.time())
    get_db().execute(
        'INSERT INTO idea_plans(id, idea_id, version, content, created_at,'
        ' updated_at) VALUES (?,?,?,?,?,?)',
        (str(ULID()), other, 2, 'A' * 9000, now, now),
    )
    get_db().commit()

    text, _event = IdeaTools.for_idea(here).run_tool('idea_read', {'title': 'Torrent tab'})
    assert 'build plan has been generated for it (v2)' in text
    assert 'AAAA' not in text


def test_a_long_idea_is_cut_and_says_so():
    from backend import recall

    _repo('r1', 'lunaschal')
    here = _idea('r1', title='Anchor')
    _idea('r1', title='A very long one', content='word ' * 4000)

    text, _event = IdeaTools.for_idea(here).run_tool(
        'idea_read', {'title': 'A very long one'})

    assert 'Cut off here' in text
    assert len(text) < recall.MAX_DOC_CHARS + 400


def test_an_ambiguous_title_is_reported_rather_than_guessed():
    _repo('r1', 'lunaschal')
    here = _idea('r1', title='Anchor')
    _idea('r1', title='Recall tools for writing')
    _idea('r1', title='Recall tools for ideas')

    text, event = IdeaTools.for_idea(here).run_tool('idea_read', {'title': 'Recall tools'})
    assert event['ok'] is False
    assert event['error'] == 'ambiguous title'
    assert 'Several ideas' in text


# --- searching --------------------------------------------------------------

def test_search_reaches_a_dictated_ideas_transcript():
    """A voice-captured idea has its whole substance in raw_content and an
    empty title — a search over the polished text alone would miss exactly the
    ideas nobody has written up yet."""
    _repo('r1', 'lunaschal')
    here = _idea('r1', title='Anchor')
    _idea('r1', raw='what if the newspaper reader remembered where I stopped')

    text, event = IdeaTools.for_idea(here).run_tool(
        'idea_search', {'query': 'newspaper reader'})

    assert event['count'] == 1
    assert 'newspaper reader' in text


def test_search_folds_case_in_any_alphabet():
    _repo('r1', 'lunaschal')
    here = _idea('r1', title='Anchor')
    _idea('r1', title='Транскрипція', content='Зробити кращий розпізнавач.')

    _text, event = IdeaTools.for_idea(here).run_tool(
        'idea_search', {'query': 'транскрипція'})
    assert event['count'] == 1


def test_the_query_is_literal_text_not_a_pattern():
    _repo('r1', 'lunaschal')
    here = _idea('r1', title='Anchor')
    _idea('r1', title='Cut costs by 50%')
    _idea('r1', title='Rename report_v2')

    _text, event = IdeaTools.for_idea(here).run_tool('idea_search', {'query': '50%'})
    assert event['count'] == 1

    _text, event = IdeaTools.for_idea(here).run_tool('idea_search', {'query': 'report_v2'})
    assert event['count'] == 1

    _text, event = IdeaTools.for_idea(here).run_tool('idea_search', {'query': '.*'})
    assert event['count'] == 0


def test_a_query_of_quotes_and_semicolons_is_answered_not_executed():
    _repo('r1', 'lunaschal')
    here = _idea('r1', title='Anchor')
    _idea('r1', title='Something real')

    text, event = IdeaTools.for_idea(here).run_tool(
        'idea_search', {'query': '"; DROP TABLE ideas; --'})

    assert event['ok'] is True
    assert 'No other idea here mentions' in text
    assert get_db().execute('SELECT COUNT(*) AS n FROM ideas').fetchone()['n'] == 2


def test_search_stays_within_the_result_budget():
    from backend import recall

    _repo('r1', 'lunaschal')
    here = _idea('r1', title='Anchor')
    for i in range(10):
        _idea('r1', title=f'Idea {i}', content='recall ' + ('filler ' * 400))

    text, event = IdeaTools.for_idea(here).run_tool(
        'idea_search', {'query': 'recall', 'limit': 10})

    assert event['count'] == 10
    assert len(text) < recall.MAX_RESULT_CHARS + recall.MAX_HIT_CHARS + 200


# --- the contract -----------------------------------------------------------

def test_every_offered_tool_is_dispatchable():
    assert TOOL_NAMES == {t['function']['name'] for t in TOOLS}
    _repo('r1', 'lunaschal')
    tools = IdeaTools.for_idea(_idea('r1', title='Anchor'))
    for name in TOOL_NAMES:
        _text, event = tools.run_tool(name, {'title': 'Anchor', 'query': 'anchor'})
        assert event['tool'] == name


def test_an_unknown_tool_is_refused_rather_than_raising():
    _repo('r1', 'lunaschal')
    tools = IdeaTools.for_idea(_idea('r1', title='Anchor'))
    _text, event = tools.run_tool('idea_delete', {})
    assert event['ok'] is False
    assert event['error'] == 'unknown tool'
