"""Exercise-name canonicalization (pure — no DB, no network)."""
import pytest

from backend.lifestyle.exercises import (
    canonicalize,
    display_name,
    normalize_exercise,
)


@pytest.mark.parametrize('raw,expected', [
    ('Bicep Curls', 'bicep curl'),
    ('  bicep   curl  ', 'bicep curl'),
    ('BICEP CURLS!!', 'bicep curl'),
    ('lat pulldowns', 'lat pulldown'),
    ('bench press', 'bench press'),          # 'ss' survives singularization
    ('bench presses', 'bench press'),
    ('flies', 'fly'),
    ('pull-ups', 'pull up'),
    ('3 sets of squats', '3 squat'),         # noise words dropped
])
def test_normalize_exercise(raw, expected):
    assert normalize_exercise(raw) == expected


@pytest.mark.parametrize('raw', ['', '   ', None, 123, '!!!', 'the'])
def test_normalize_rejects_unusable_names(raw):
    assert normalize_exercise(raw) == ''


def test_canonicalize_folds_plural_and_case_onto_existing():
    known = ['bicep curl']
    assert canonicalize('Bicep Curls', known) == 'bicep curl'
    assert canonicalize('bicep curl', known) == 'bicep curl'


def test_canonicalize_folds_shorthand_onto_longer_known_name():
    # The doc's motivating case: "curls" has to land on "bicep curl".
    assert canonicalize('curls', ['bicep curl']) == 'bicep curl'


def test_a_more_specific_name_starts_its_own_series():
    # Folding is one-directional on purpose: "squat" -> "barbell squat" is an
    # abbreviation, but "hack squat machine" -> "squat" would destroy a real
    # distinction. Splitting is recoverable via POST /exercises/merge.
    assert canonicalize('barbell squats', ['squat']) == 'barbell squat'
    assert canonicalize('hack squat machine', ['squat']) == 'hack squat machine'


def test_canonicalize_keeps_genuinely_different_lifts_apart():
    known = ['front squat']
    assert canonicalize('back squat', known) == 'back squat'
    assert canonicalize('overhead press', ['bench press']) == 'overhead press'


def test_ambiguous_shorthand_takes_the_first_known_candidate():
    # `known` arrives most-used-first, so the exercise actually trained wins.
    assert canonicalize('squat', ['barbell squat', 'front squat']) == 'barbell squat'
    assert canonicalize('squat', ['front squat', 'barbell squat']) == 'front squat'


def test_canonicalize_absorbs_spelling_drift():
    # Fuzzy match catches a word split that normalization alone cannot.
    assert canonicalize('deadlift', ['dead lift']) == 'dead lift'


def test_singularization_alone_merges_tricep_and_triceps():
    assert normalize_exercise('triceps pushdown') == normalize_exercise('tricep pushdown')


def test_hyphen_and_space_spellings_reach_the_same_series():
    assert canonicalize('pull ups', ['pull up']) == 'pull up'


def test_canonicalize_coins_a_new_name_when_nothing_is_close():
    assert canonicalize('Romanian Deadlifts', ['bicep curl']) == 'romanian deadlift'
    assert canonicalize('Romanian Deadlifts', []) == 'romanian deadlift'


def test_canonicalize_rejects_unusable_names():
    assert canonicalize('!!!', ['bicep curl']) == ''
    assert canonicalize(None, ['bicep curl']) == ''


def test_display_name_title_cases():
    assert display_name('bicep curl') == 'Bicep Curl'
    assert display_name('') == ''
