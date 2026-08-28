"""Evidence gathering and the clamp that keeps "already built" honest."""
import json

import pytest

from backend.db.connection import get_db
from backend.research import assess, evidence as ev
from backend.research.repo_job import run_repo_snapshot


# --- Keyword extraction (pure) ---

def test_keywords_drop_stopwords_and_short_words():
    words = ev.keywords('I want to add a habit grid to the journal')
    assert 'habit' in words
    assert 'grid' in words
    assert 'journal' in words
    assert 'want' not in words and 'add' not in words and 'the' not in words


def test_keywords_stem_plurals_so_sketches_matches_sketch():
    words = ev.keywords('attach sketches to ideas')
    assert 'sketch' in words and 'sketches' in words
    assert 'idea' in words


def test_identifier_words_split_paths_and_camel_case():
    assert 'paper' in ev._identifier_words('/api/paper/pages/<id>')
    assert 'sketch' in ev._identifier_words('SketchStrip.tsx')
    assert 'wiki' in ev._identifier_words('wiki_articles')


# --- Candidate gathering ---

FACTS = {
    'tables': [
        {'table': 'paper_pages', 'virtual': False,
         'columns': [{'name': 'id'}, {'name': 'strokes'}, {'name': 'image_path'}]},
        {'table': 'journal_fts', 'virtual': True, 'columns': [{'name': 'id'}]},
        {'table': 'calorie_logs', 'virtual': False, 'columns': [{'name': 'calories'}]},
    ],
    'routes': [
        {'method': 'GET', 'path': '/api/paper/pages/<id>/image', 'function': 'page_image',
         'file': 'backend/routes/paper.py', 'line': 120, 'doc': 'PNG snapshot.'},
        {'method': 'GET', 'path': '/api/tasks/todos', 'function': 'list_todos',
         'file': 'backend/routes/tasks.py', 'line': 20, 'doc': None},
    ],
    'components': [{'file': 'src/components/Paper/PaperCanvas.tsx', 'lines': 700, 'exports': []}],
    'api': [{'namespace': 'paper', 'methods': ['list', 'getPage']}],
    'ai': [{'module': 'workouts', 'purpose': 'Freeform gym log parsing.'}],
    'settings': ['llama_url', 'briefing_hour'],
    'views': {'navItems': [{'view': 'paper', 'label': 'Paper'}]},
    'docs': [{'path': 'docs/ROADMAP.md',
              'items': ['Handwriting OCR so pages become searchable',
                        'Habit tracking with streaks']}],
}


def test_candidates_surface_the_relevant_table_and_route():
    idea = {'title': 'Paper page export', 'content': 'export a paper page as an image'}
    kinds = {(c['kind'], c['ref']) for c in ev.gather_candidates(idea, FACTS)}
    assert ('table', 'paper_pages') in kinds
    assert ('route', 'GET /api/paper/pages/<id>/image') in kinds


def test_candidates_skip_fts_shadow_tables():
    idea = {'title': 'journal search', 'content': 'full text search the journal'}
    assert not any(
        c['ref'] == 'journal_fts' for c in ev.gather_candidates(idea, FACTS)
    )


def test_an_unrelated_idea_surfaces_nothing():
    idea = {'title': 'Bluetooth thermometer', 'content': 'pair a bluetooth thermometer'}
    assert ev.gather_candidates(idea, FACTS) == []


def test_an_empty_idea_surfaces_nothing():
    assert ev.gather_candidates({'title': '', 'content': ''}, FACTS) == []


def test_every_candidate_carries_a_real_location():
    idea = {'title': 'Paper export', 'content': 'export paper pages'}
    for candidate in ev.gather_candidates(idea, FACTS):
        assert candidate['file'], candidate
        assert candidate['kind'] and candidate['ref']


def test_candidate_count_is_capped():
    idea = {'title': 'paper page image', 'content': 'paper page image calorie todo workout'}
    assert len(ev.gather_candidates(idea, FACTS, limit=2)) == 2


def test_roadmap_matches_are_separate_from_code_evidence():
    """Being on the roadmap means planned, which is the opposite of built."""
    idea = {'title': 'Habit tracking', 'content': 'habit streaks'}
    assert ev.roadmap_matches(idea, FACTS) == ['Habit tracking with streaks']
    # And it does not leak into the code candidates.
    assert all(c['kind'] != 'roadmap' for c in ev.gather_candidates(idea, FACTS))


# --- Index selection ---

def test_select_by_index_is_one_based():
    candidates = [{'ref': 'a'}, {'ref': 'b'}, {'ref': 'c'}]
    assert ev.select_by_index(candidates, [1, 3]) == [{'ref': 'a'}, {'ref': 'c'}]


def test_select_by_index_drops_out_of_range_and_junk():
    """A stray index is the model miscounting, not a reason to lose the whole
    assessment."""
    candidates = [{'ref': 'a'}, {'ref': 'b'}]
    assert ev.select_by_index(candidates, [0, 3, 99, -1, 'x', None, 2]) == [{'ref': 'b'}]


def test_select_by_index_deduplicates():
    candidates = [{'ref': 'a'}, {'ref': 'b'}]
    assert ev.select_by_index(candidates, [1, 1, 1]) == [{'ref': 'a'}]


def test_render_candidates_is_numbered_from_one():
    text = ev.render_candidates([
        {'kind': 'table', 'ref': 'ideas', 'file': 'backend/db/schema.sql',
         'line': None, 'detail': 'id, title'},
        {'kind': 'route', 'ref': 'GET /api/ideas', 'file': 'backend/routes/ideas.py',
         'line': 30, 'detail': None},
    ])
    assert text.startswith('1. [table] ideas (backend/db/schema.sql) — id, title')
    assert '2. [route] GET /api/ideas (backend/routes/ideas.py:30)' in text


# --- The clamp ---

def _evidence(n):
    return [{'kind': 'table', 'ref': f't{i}', 'file': 'f'} for i in range(n)]


def test_a_yes_citing_nothing_is_clamped_to_no():
    """The single most important test here: a confident, uncited "yes" is the
    one output that could make the user drop an idea they should build."""
    out = assess.clamp(
        {'verdict': 'yes', 'confidence': 0.95, 'rationale': 'It exists.'},
        evidence=[], has_snapshot=True,
    )
    assert out['verdict'] == 'no'
    assert out['confidence'] <= 0.4


def test_a_yes_with_one_citation_is_downgraded_to_partial():
    out = assess.clamp(
        {'verdict': 'yes', 'confidence': 0.9, 'rationale': 'r'},
        evidence=_evidence(1), has_snapshot=True,
    )
    assert out['verdict'] == 'partial'
    assert out['confidence'] <= 0.6


def test_a_yes_with_two_citations_stands():
    out = assess.clamp(
        {'verdict': 'yes', 'confidence': 0.8, 'rationale': 'r'},
        evidence=_evidence(2), has_snapshot=True,
    )
    assert out['verdict'] == 'yes'
    assert out['confidence'] == 0.8


def test_no_snapshot_means_no_verdict_at_all():
    out = assess.clamp(
        {'verdict': 'yes', 'confidence': 1.0, 'rationale': 'sure'},
        evidence=_evidence(5), has_snapshot=False,
    )
    assert out['verdict'] == 'no'
    assert out['confidence'] == 0.0
    assert 'repo-context scan' in out['rationale']


def test_clamp_survives_garbage_from_the_model():
    out = assess.clamp(
        {'verdict': 'definitely', 'confidence': 'very', 'effort': 'xxl'},
        evidence=_evidence(2), has_snapshot=True,
    )
    assert out['verdict'] == 'no'
    assert out['confidence'] == 0.0
    assert out['effort'] is None


def test_confidence_is_bounded_and_rounded():
    assert assess.clamp({'verdict': 'partial', 'confidence': 5.0, 'rationale': 'r'},
                        _evidence(2), True)['confidence'] == 1.0
    assert assess.clamp({'verdict': 'partial', 'confidence': -2, 'rationale': 'r'},
                        _evidence(2), True)['confidence'] == 0.0
    assert assess.clamp({'verdict': 'partial', 'confidence': 0.6789, 'rationale': 'r'},
                        _evidence(2), True)['confidence'] == 0.68


# --- Rationale prose ---
#
# The strings below are verbatim from the first live run against Gemma 4: told
# to cite by candidate number, it put the numbers in the rationale too, and the
# candidate list is prompt-internal, so the UI rendered bare pointers to nothing.

def test_bracketed_index_citations_are_stripped_from_the_rationale():
    out = assess.clamp(
        {'verdict': 'partial', 'confidence': 0.9,
         'rationale': 'The backend supports the necessary data structures '
                      '[1, 13, 15]. The routes for reading files [3, 25] are generic.'},
        evidence=_evidence(3), has_snapshot=True,
    )
    assert out['rationale'] == (
        'The backend supports the necessary data structures. '
        'The routes for reading files are generic.'
    )


def test_parenthesised_index_citations_are_stripped_too():
    out = assess.clamp(
        {'verdict': 'partial', 'confidence': 0.9,
         'rationale': 'It structures the text into sets (9), stores them (8, 10, 11), '
                      'and exposes progression (14).'},
        evidence=_evidence(4), has_snapshot=True,
    )
    assert out['rationale'] == (
        'It structures the text into sets, stores them, and exposes progression.'
    )


def test_a_parenthetical_that_says_something_keeps_its_number():
    """Only bare numbers read as citations; "(2 tables)" is prose."""
    out = assess.clamp(
        {'verdict': 'partial', 'confidence': 0.5,
         'rationale': 'Two tables (2 tables in total) cover it, kept for 30 days.'},
        evidence=_evidence(2), has_snapshot=True,
    )
    assert out['rationale'] == 'Two tables (2 tables in total) cover it, kept for 30 days.'


# --- End to end ---

@pytest.fixture
def snapshot(client, monkeypatch):
    import backend.research.repo_job as job
    monkeypatch.setattr(job, 'summarize_delta', lambda *a, **k: None)
    run_repo_snapshot(force=True)


def _idea(client, title='Paper sketches on ideas', content='attach a paper page to an idea'):
    return client.post('/api/ideas', json={'title': title, 'rawContent': content}).get_json()['id']


def test_run_assessment_stores_a_clamped_verdict(client, snapshot, monkeypatch):
    import backend.research.assess as mod
    monkeypatch.setattr(mod, 'assess_idea', lambda *a, **k: {
        'verdict': 'yes', 'confidence': 0.99, 'rationale': 'Already there.',
        'evidenceIndexes': [1], 'openQuestions': [],
    })
    idea_id = _idea(client)
    result = mod.run_assessment(idea_id)

    assert result['verdict'] == 'partial', 'one citation is not enough for yes'
    assert result['confidence'] <= 0.6
    assert len(json.loads(result['evidence'])) == 1
    # assessed_at is in TIMESTAMP_COLS, so it comes back as ISO.
    assert result['assessedAt'].startswith('20')


def test_assessment_records_the_snapshot_it_judged_against(client, snapshot, monkeypatch):
    import backend.research.assess as mod
    monkeypatch.setattr(mod, 'assess_idea', lambda *a, **k: {
        'verdict': 'no', 'confidence': 0.2, 'rationale': 'r',
        'evidenceIndexes': [], 'openQuestions': [],
    })
    idea_id = _idea(client)
    result = mod.run_assessment(idea_id)
    assert result['snapshotId']
    assert mod.is_stale(result, mod.current_snapshot()) is False

    # A newer snapshot makes the old verdict stale.
    run_repo_snapshot(force=True)
    assert mod.is_stale(result, mod.current_snapshot()) is True


def test_assessment_without_a_snapshot_says_so(client, monkeypatch):
    import backend.research.assess as mod
    monkeypatch.setattr(mod, 'assess_idea', lambda *a, **k: {
        'verdict': 'yes', 'confidence': 1.0, 'rationale': 'trust me',
        'evidenceIndexes': [], 'openQuestions': [],
    })
    result = mod.run_assessment(_idea(client))
    assert result['verdict'] == 'no'
    assert 'repo-context scan' in result['rationale']


def test_an_unavailable_model_still_produces_an_honest_row(client, snapshot, monkeypatch):
    import backend.research.assess as mod
    monkeypatch.setattr(mod, 'assess_idea', lambda *a, **k: None)
    result = mod.run_assessment(_idea(client))
    assert result['verdict'] == 'no'
    assert result['confidence'] == 0.0


def test_open_questions_are_created_then_not_resurrected(client, snapshot, monkeypatch):
    import backend.research.assess as mod
    questions = [{'question': 'Where should sketches live?', 'why': 'storage',
                  'options': ['Paper', 'new table']}]
    monkeypatch.setattr(mod, 'assess_idea', lambda *a, **k: {
        'verdict': 'no', 'confidence': 0.3, 'rationale': 'r',
        'evidenceIndexes': [], 'openQuestions': questions,
    })
    idea_id = _idea(client)
    mod.run_assessment(idea_id)
    assert mod.open_question_count(idea_id) == 1

    # The user answers it.
    get_db().execute(
        "UPDATE idea_questions SET status='answered', answer='Paper pages' WHERE idea_id=?",
        (idea_id,),
    )
    get_db().commit()

    # A re-run proposing the same question must not reopen it.
    mod.run_assessment(idea_id)
    assert mod.open_question_count(idea_id) == 0
    assert mod.answered_questions(idea_id) == [
        {'question': 'Where should sketches live?', 'answer': 'Paper pages'}
    ]


def test_question_key_normalizes_wording():
    assert assess.question_key('Where should sketches live?') == assess.question_key(
        '  where should SKETCHES live  '
    )
    assert assess.question_key('Different question?') != assess.question_key('Another one?')
    assert assess.question_key('') == ''


def test_run_assessment_on_a_missing_idea(client):
    assert assess.run_assessment('nope') is None


# --- Decision options (pure) ---
#
# A decision is rendered as a multiple choice with a write-your-own last row,
# so what reaches the UI has to be a real, mutually exclusive list.

def test_options_are_trimmed_and_blanks_dropped():
    assert assess.normalize_options(['  Paper  ', '', '   ', 'A new table']) == [
        'Paper', 'A new table'
    ]


def test_options_deduplicate_ignoring_case_and_punctuation():
    assert assess.normalize_options(['Paper pages', 'paper pages!', 'PAPER  PAGES']) == [
        'Paper pages'
    ]


def test_the_models_own_escape_hatch_is_dropped():
    # The UI's last choice is already "Something else"; two of them means one
    # that does nothing.
    assert assess.normalize_options(['Paper', 'A new table', 'Other']) == [
        'Paper', 'A new table'
    ]
    assert assess.normalize_options(['Paper', 'Something else']) == ['Paper']
    assert assess.normalize_options(['Paper', 'Other (please specify)']) == ['Paper']


def test_non_strings_are_not_options():
    assert assess.normalize_options(['Paper', 3, None, {'a': 1}]) == ['Paper']
    assert assess.normalize_options(None) == []


def test_stored_options_survive_the_round_trip(client, snapshot, monkeypatch):
    import backend.research.assess as mod
    monkeypatch.setattr(mod, 'assess_idea', lambda *a, **k: {
        'verdict': 'no', 'confidence': 0.3, 'rationale': 'r', 'evidenceIndexes': [],
        'openQuestions': [{'question': 'Where should sketches live?',
                           'options': ['Paper', ' Paper ', 'A new table', 'Other']}],
    })
    idea_id = _idea(client)
    mod.run_assessment(idea_id)

    question = client.get(f'/api/ideas/{idea_id}/questions').get_json()[0]
    assert question['options'] == ['Paper', 'A new table']


def test_a_question_with_no_usable_options_still_reaches_the_user(client, snapshot, monkeypatch):
    """A free-text decision is worse than a multiple choice, but far better
    than a decision that is silently dropped."""
    import backend.research.assess as mod
    monkeypatch.setattr(mod, 'assess_idea', lambda *a, **k: {
        'verdict': 'no', 'confidence': 0.3, 'rationale': 'r', 'evidenceIndexes': [],
        'openQuestions': [{'question': 'Where should sketches live?', 'options': ['Other']}],
    })
    idea_id = _idea(client)
    mod.run_assessment(idea_id)

    question = client.get(f'/api/ideas/{idea_id}/questions').get_json()[0]
    assert question['options'] == []
    assert question['status'] == 'open'
