"""A Writing discussion reaching its own project — and nothing else.

The scope is the load-bearing part: these tools are mounted on a turn whose
system prompt was written by the browser, and the project id they are bound to
is asserted by that browser too. So the tests that matter most are the ones
that pin what happens at the edges of the scope.
"""
import time

import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.writing.tools import TOOL_NAMES, TOOLS, WritingTools

pytestmark = pytest.mark.usefixtures('client')


def _project(title='The Salt Roads'):
    now = int(time.time())
    pid = str(ULID())
    get_db().execute(
        'INSERT INTO writing_projects(id, title, description, created_at, updated_at)'
        ' VALUES (?,?,?,?,?)',
        (pid, title, '', now, now),
    )
    get_db().commit()
    return pid


def _chapter(project_id, title, content='', position=0):
    now = int(time.time())
    cid = str(ULID())
    get_db().execute(
        'INSERT INTO writing_chapters(id, project_id, title, content, position,'
        ' created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
        (cid, project_id, title, content, position, now, now),
    )
    get_db().commit()
    return cid


def _note(project_id, title, content='', doc_type='note'):
    now = int(time.time())
    nid = str(ULID())
    get_db().execute(
        'INSERT INTO writing_context_docs(id, project_id, title, content,'
        ' doc_type, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
        (nid, project_id, title, content, doc_type, now, now),
    )
    get_db().commit()
    return nid


@pytest.fixture
def project():
    pid = _project()
    _chapter(pid, 'The Ferry', 'Мірена closed the door. ' * 40, position=0)
    _chapter(pid, 'What Mirena Knew', 'word ' * 4000, position=1)
    _chapter(pid, 'Untitled', '', position=2)
    _note(pid, 'Mirena', 'A ferrywoman who lies well.', doc_type='character')
    _note(pid, 'The Salt Roads', 'Trade routes, 50% tariff at the border.',
          doc_type='worldbuilding')
    return pid


# --- scope ------------------------------------------------------------------

def test_a_project_sees_only_its_own_chapters_and_notes(project):
    other = _project('Someone Else’s Book')
    _chapter(other, 'The Harbour', 'Nothing to do with the first book.')
    _note(other, 'Kasia', 'Another story’s character.')

    text, event = WritingTools(project).run_tool('writing_list', {})
    assert 'The Ferry' in text and 'Mirena' in text
    assert 'The Harbour' not in text and 'Kasia' not in text
    assert event['count'] == 5

    text, event = WritingTools(other).run_tool('writing_list', {})
    assert 'The Harbour' in text
    assert 'The Ferry' not in text
    assert event['count'] == 2


def test_a_title_from_another_project_is_not_found_rather_than_opened(project):
    other = _project('Someone Else’s Book')
    _chapter(other, 'The Harbour', 'Secret.')

    text, event = WritingTools(project).run_tool('writing_read', {'title': 'The Harbour'})
    assert event['ok'] is False
    assert event['error'] == 'not found'
    assert 'Secret' not in text


def test_another_projects_text_is_unreachable_by_search(project):
    other = _project('Someone Else’s Book')
    _chapter(other, 'The Harbour', 'Мірена appears here too.')

    text, event = WritingTools(project).run_tool('writing_search', {'query': 'Мірена'})
    assert event['ok'] is True
    assert 'The Harbour' not in text


def test_an_unscoped_instance_returns_nothing_rather_than_everything(project):
    """The opposite of WikiTools(None), deliberately: `project_id = NULL` is
    never true in SQL, so this fails closed. Pinned so a later refactor toward
    the wiki's union semantics fails here rather than in production."""
    tools = WritingTools(None)

    text, event = tools.run_tool('writing_list', {})
    assert event['count'] == 0
    assert 'The Ferry' not in text

    _text, event = tools.run_tool('writing_search', {'query': 'Мірена'})
    assert event['count'] == 0

    _text, event = tools.run_tool('writing_read', {'title': 'The Ferry'})
    assert event['ok'] is False


# --- the index --------------------------------------------------------------

def test_the_index_carries_both_kinds_in_the_order_the_author_sees(project):
    text, _event = WritingTools(project).run_tool('writing_list', {})

    assert text.index('The Ferry') < text.index('What Mirena Knew')
    assert '[character] Mirena' in text
    assert '[worldbuilding] The Salt Roads' in text


def test_an_empty_chapter_is_listed_as_empty(project):
    """A placeholder chapter is a fact worth having — it is what the author has
    not written yet."""
    text, _event = WritingTools(project).run_tool('writing_list', {})
    assert 'Untitled — empty' in text


def test_a_project_with_nothing_in_it_says_so():
    text, event = WritingTools(_project()).run_tool('writing_list', {})
    assert 'no chapters or notes yet' in text
    assert event['ok'] is True and event['count'] == 0


# --- reading ----------------------------------------------------------------

def test_reading_a_chapter_names_what_was_opened(project):
    text, event = WritingTools(project).run_tool('writing_read', {'title': 'the ferry'})

    assert event['ok'] is True
    assert event['kind'] == 'chapter'
    assert event['title'] == 'The Ferry'       # resolved, not what was typed
    assert event['arg'] == 'the ferry'
    assert 'Мірена closed the door' in text


def test_reading_a_note_reports_it_as_a_note(project):
    _text, event = WritingTools(project).run_tool('writing_read', {'title': 'Mirena'})
    assert event['kind'] == 'note'


def test_a_long_chapter_is_cut_and_says_how_much_is_missing(project):
    from backend import recall

    text, event = WritingTools(project).run_tool(
        'writing_read', {'title': 'What Mirena Knew'})

    assert event['ok'] is True
    assert len(text) < recall.MAX_DOC_CHARS + 400
    assert 'Cut off here' in text
    assert '20,000 characters' in text          # the real total, not the shown one
    assert 'writing_search' in text             # points at what can reach the rest


def test_an_empty_chapter_reads_as_empty_not_as_an_error(project):
    text, event = WritingTools(project).run_tool('writing_read', {'title': 'Untitled'})
    assert event['ok'] is True
    assert 'is empty' in text


def test_an_ambiguous_title_is_reported_rather_than_guessed(project):
    _chapter(project, 'The Ferry Returns', 'Later.', position=3)

    text, event = WritingTools(project).run_tool('writing_read', {'title': 'Ferry'})
    assert event['ok'] is False
    assert event['error'] == 'ambiguous title'
    assert 'Several things' in text


# --- searching --------------------------------------------------------------

def test_search_finds_a_name_in_the_body_of_a_chapter(project):
    text, event = WritingTools(project).run_tool('writing_search', {'query': 'closed the door'})
    assert event['count'] == 1
    assert 'chapter 1 — The Ferry' in text


def test_search_folds_case_in_any_alphabet(project):
    """SQLite's LIKE would match this at one casing and not the other, silently."""
    _text, event = WritingTools(project).run_tool('writing_search', {'query': 'мірена'})
    assert event['count'] >= 1


def test_a_title_match_comes_before_a_body_match(project):
    """A hit on the name of a thing is almost always the one wanted, and
    chapters would otherwise always sort ahead of notes."""
    _chapter(project, 'The Crossing', 'She thought of the Salt Roads often.',
             position=3)

    text, event = WritingTools(project).run_tool(
        'writing_search', {'query': 'Salt Roads'})

    assert event['count'] == 2
    assert text.index('worldbuilding — The Salt Roads') < text.index('chapter 4 — The Crossing')


def test_the_query_is_literal_text_not_a_pattern(project):
    """`%` and `_` are characters here. The test is also what stops a later
    switch back to SQL LIKE from silently reintroducing them as wildcards."""
    _text, event = WritingTools(project).run_tool('writing_search', {'query': '50%'})
    assert event['count'] == 1

    _text, event = WritingTools(project).run_tool('writing_search', {'query': '%'})
    assert event['count'] == 1        # matches the tariff note only, not everything


def test_a_query_of_quotes_and_semicolons_is_answered_not_executed(project):
    text, event = WritingTools(project).run_tool(
        'writing_search', {'query': '"; DROP TABLE writing_chapters; --'})

    assert event['ok'] is True
    assert 'Nothing in this project matches' in text
    assert get_db().execute(
        'SELECT COUNT(*) AS n FROM writing_chapters').fetchone()['n'] == 3


def test_a_blank_query_returns_nothing_rather_than_the_whole_project(project):
    text, event = WritingTools(project).run_tool('writing_search', {'query': '   '})
    assert event['count'] == 0
    assert 'No search terms' in text


def test_search_stays_within_the_result_budget():
    from backend import recall

    pid = _project()
    for i in range(10):
        _chapter(pid, f'Chapter {i}', 'mirena ' + ('filler ' * 500), position=i)

    text, event = WritingTools(pid).run_tool(
        'writing_search', {'query': 'mirena', 'limit': 10})

    assert event['count'] == 10
    assert len(text) < recall.MAX_RESULT_CHARS + recall.MAX_HIT_CHARS + 200


# --- the contract -----------------------------------------------------------

def test_every_offered_tool_is_dispatchable(project):
    assert TOOL_NAMES == {t['function']['name'] for t in TOOLS}
    tools = WritingTools(project)
    for name in TOOL_NAMES:
        _text, event = tools.run_tool(name, {'title': 'The Ferry', 'query': 'ferry'})
        assert event['tool'] == name


def test_an_unknown_tool_is_refused_rather_than_raising(project):
    _text, event = WritingTools(project).run_tool('writing_delete', {})
    assert event['ok'] is False
    assert event['error'] == 'unknown tool'
