"""The shared recall machinery: budgets, clipping, and case-insensitive search.

No database. These are the rules three toolboxes now depend on, and the two
that are easy to get quietly wrong are the budget (a half-hit reads as the
whole of what was written) and the case folding (SQL's is ASCII-only, which
fails silently in any other alphabet).
"""
from backend import recall


# --- the budget -------------------------------------------------------------

def test_hits_are_whole_or_absent():
    blocks = ['x' * 1000, 'y' * 1000, 'z' * 1000]
    out = recall.join(blocks)

    assert 'x' * 1000 in out and 'y' * 1000 in out
    assert 'z' not in out
    assert '(1 more not shown)' in out


def test_one_oversized_hit_is_still_returned():
    """Better one too-long block than an empty result that looks like no match."""
    out = recall.join(['x' * (recall.MAX_RESULT_CHARS + 500)])
    assert out.startswith('x')
    assert 'not shown' not in out


def test_everything_fitting_says_nothing_about_omissions():
    out = recall.join(['short', 'also short'])
    assert out == 'short\n\nalso short'


def test_clip_collapses_whitespace_and_marks_the_cut():
    assert recall.clip('a   b\n\nc') == 'a b c'
    assert recall.clip('x' * 50, limit=10) == 'x' * 10 + '…'


def test_limit_is_clamped_whatever_arrives():
    assert recall.limit_of({}) == recall.DEFAULT_LIMIT
    # 0 is falsy, so it reads as "unspecified" rather than as "none of them" —
    # a zero-hit search would be a tool call that cannot answer anything.
    assert recall.limit_of({'limit': 0}) == recall.DEFAULT_LIMIT
    assert recall.limit_of({'limit': -4}) == 1
    assert recall.limit_of({'limit': 999}) == recall.MAX_LIMIT
    assert recall.limit_of({'limit': 'abc'}) == recall.DEFAULT_LIMIT
    assert recall.limit_of({'limit': None}) == recall.DEFAULT_LIMIT
    assert recall.limit_of(None) == recall.DEFAULT_LIMIT


# --- reading one document ---------------------------------------------------

def test_a_short_document_is_not_reported_as_truncated():
    text, truncated = recall.clip_doc('The ferry left at dawn.')
    assert text == 'The ferry left at dawn.'
    assert truncated is False


def test_a_long_document_is_cut_and_says_so():
    text, truncated = recall.clip_doc('word ' * 5000)
    assert truncated is True
    assert len(text) <= recall.MAX_DOC_CHARS


def test_the_cut_never_lands_mid_word():
    text, _ = recall.clip_doc('alpha bravo charlie ' * 500, limit=50)
    assert not text.endswith('alph')
    assert text.split()[-1] in ('alpha', 'bravo', 'charlie')


def test_a_paragraph_break_near_the_end_wins_over_a_word_break():
    body = ('a' * 40) + '\n\n' + ('b' * 40)
    text, truncated = recall.clip_doc(body, limit=50)
    assert truncated is True
    assert text == 'a' * 40


# --- case-insensitive search ------------------------------------------------

def test_search_folds_case_outside_ascii():
    """SQLite's LIKE folds A-Z only, so this is the whole reason matching
    happens in Python rather than in the query."""
    assert recall.find_ci('Мірена зачинила двері', 'мірена') == 0
    assert recall.find_ci('мірена зачинила двері', 'МІРЕНА') == 0
    assert recall.find_ci('Страва була солона', 'солона') is not None


def test_search_folds_ascii_case_too():
    assert recall.find_ci('The Salt Roads', 'salt roads') == 4


def test_a_needle_that_is_not_there_is_not_found():
    assert recall.find_ci('The ferry left', 'harbour') is None
    assert recall.find_ci('', 'x') is None
    assert recall.find_ci('x', '') is None


def test_the_query_is_matched_literally_not_as_a_pattern():
    """The needle is the user's words. `50%` means those characters."""
    assert recall.find_ci('50% off today', '50%') == 0
    assert recall.find_ci('report_v2 is ready', 'report_v2') == 0
    assert recall.find_ci('reportXv2 is ready', 'report_v2') is None
    assert recall.find_ci('anything at all', '.*') is None


def test_the_offset_survives_characters_that_fold_to_a_different_length():
    """casefold() turns ß into ss, so an index into folded text is not an index
    into the original — every excerpt after one would be cut in the wrong
    place. find_ci searches the original."""
    body = 'Straße Straße mirena'
    at = recall.find_ci(body, 'mirena')
    assert body[at:at + 6] == 'mirena'


# --- excerpts ---------------------------------------------------------------

def test_an_excerpt_is_centred_on_the_match_not_on_the_opening():
    body = ('filler ' * 400) + 'MIRENA KNEW' + (' filler' * 400)
    out = recall.excerpt(body, 'mirena knew')

    assert 'MIRENA KNEW' in out
    assert out.startswith('…')


def test_an_excerpt_of_a_title_only_hit_falls_back_to_the_opening():
    body = 'The ferry left at dawn and nobody saw it go.'
    out = recall.excerpt(body, 'harbour')
    assert out.startswith('The ferry left')


def test_an_excerpt_from_the_very_start_has_no_leading_ellipsis():
    out = recall.excerpt('Mirena knew the whole time.', 'Mirena')
    assert not out.startswith('…')


# --- scope ------------------------------------------------------------------

def test_an_unset_scope_needs_a_different_operator():
    """`column = NULL` is never true, which is the whole reason this exists."""
    assert recall.scope_clause('repo_id', None) == ('repo_id IS NULL', [])
    assert recall.scope_clause('repo_id', 'r1') == ('repo_id = ?', ['r1'])


# --- resolving a title ------------------------------------------------------

def _titles(*names):
    return [{'title': n} for n in names]


def _title_of(row):
    return row['title']


def test_an_exact_title_wins_over_a_longer_one_containing_it():
    rows = _titles('The Ferry', 'The Ferry Home')
    row, reason = recall.pick_one(rows, 'the ferry', _title_of)
    assert row['title'] == 'The Ferry'
    assert reason == ''


def test_a_unique_substring_resolves():
    rows = _titles('What Mirena Knew', 'The Salt Roads')
    row, _ = recall.pick_one(rows, 'mirena', _title_of)
    assert row['title'] == 'What Mirena Knew'


def test_two_matches_are_ambiguous_rather_than_the_first_one():
    """Two chapters called "Untitled" is a normal state of a draft. Picking one
    silently means the model reads one thing believing it read another."""
    rows = _titles('Untitled', 'Untitled')
    row, reason = recall.pick_one(rows, 'untitled', _title_of)
    assert row is None
    assert reason == 'ambiguous'

    row, reason = recall.pick_one(_titles('Chapter One', 'Chapter Two'),
                                  'chapter', _title_of)
    assert row is None
    assert reason == 'ambiguous'


def test_nothing_matching_says_not_found():
    row, reason = recall.pick_one(_titles('The Ferry'), 'harbour', _title_of)
    assert row is None
    assert reason == 'not found'


def test_an_empty_title_is_refused_rather_than_matching_everything():
    row, reason = recall.pick_one(_titles('The Ferry'), '  ', _title_of)
    assert row is None
    assert reason == 'no title given'


# --- the unknown-tool tail --------------------------------------------------

def test_an_unknown_tool_is_refused_rather_than_raising():
    text, event = recall.unknown_tool('writing_delete')
    assert 'writing_delete' in text
    assert event == {'tool': 'writing_delete', 'ok': False, 'error': 'unknown tool'}
