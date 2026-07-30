"""Exercise-name canonicalization for the workout log.

"bicep curls", "bicep curl" and "curls" all have to land on one series or the
progression chart means nothing (docs/lifestyle-tab.md §2). This is the same
shape of problem `backend/tags.py` solves for tags — normalize first, then fold
onto an existing entry rather than silently minting a near-duplicate.

Pure: no DB, no network. The caller supplies the canonical names already in use
(most-used first, so a generic word like "press" folds onto whichever variant
the user actually trains) and gets back the name to store.
"""
import re
from difflib import SequenceMatcher

# Above this SequenceMatcher ratio two names are treated as the same exercise.
# Tuned to catch typos and spelling drift ("deadlift"/"dead lift", "tricep"/
# "triceps") without merging genuinely different lifts — "front squat" vs "back
# squat" scores ~0.80, so the bar sits above that.
FUZZY_THRESHOLD = 0.87

# Punctuation becomes a separator rather than being deleted, so "pull-ups" and
# "pull ups" reach the same canonical form instead of splitting the series.
_PUNCT = re.compile(r'[^a-z0-9\s]+')
_WS = re.compile(r'\s+')

# Equipment/qualifier words that never carry meaning on their own. They still
# take part in matching, but a name made of nothing else is not a real exercise.
_NOISE = frozenset({'the', 'a', 'of', 'x', 'set', 'sets', 'rep', 'reps'})


def _singularize(word: str) -> str:
    """Crude English singularization, enough for gym vocabulary.

    Deliberately not a stemmer: "press" and "lats" both have to survive, so the
    rules stay narrow rather than clever.
    """
    # Consistency matters more than linguistic correctness here: "abs" folding to
    # "ab" is harmless as long as "ab" folds there too, but leaving "ups" alone
    # would split "pull-ups" from "pull up".
    if len(word) <= 2 or word.endswith('ss'):
        return word
    if word.endswith('ies'):
        return word[:-3] + 'y'
    if word.endswith(('ches', 'shes', 'xes', 'zes', 'sses')):
        return word[:-2]
    if word.endswith('s'):
        return word[:-1]
    return word


def normalize_exercise(name) -> str:
    """The canonical form of a brand-new exercise name: lowercase, punctuation
    stripped, whitespace collapsed, each word singularized. Returns '' for
    anything that isn't a usable name."""
    if not isinstance(name, str):
        return ''
    text = _PUNCT.sub(' ', name.strip().lower())
    words = [_singularize(w) for w in _WS.sub(' ', text).split(' ') if w]
    words = [w for w in words if w and w not in _NOISE]
    return ' '.join(words)


def _tokens(name: str) -> frozenset[str]:
    return frozenset(name.split(' ')) - {''}


def canonicalize(name, known=()) -> str:
    """Fold `name` onto the closest name in `known`, or coin a new canonical form.

    `known` holds canonical names already in use, most-preferred first — when a
    short name is a subset of several ("squat" under both "barbell squat" and
    "front squat"), the earlier candidate wins. Returns '' when `name` isn't a
    usable exercise name, which the caller should treat as "skip this line".
    """
    target = normalize_exercise(name)
    if not target:
        return ''

    candidates = [k for k in (normalize_exercise(k) for k in known) if k]
    if target in candidates:
        return target

    # A name entirely contained in a known one is the "curls" -> "bicep curl"
    # case: an abbreviation of something already trained. Stronger evidence than
    # any string similarity, so it's checked before fuzzy matching.
    #
    # Deliberately one-directional. Folding the other way — a *more specific*
    # new name onto a shorter known one — would swallow "hack squat machine"
    # into "squat". Under-merging costs one POST /exercises/merge; over-merging
    # silently destroys the distinction, so it splits and lets the user decide.
    target_tokens = _tokens(target)
    for candidate in candidates:
        if target_tokens <= _tokens(candidate):
            return candidate

    best, best_ratio = None, 0.0
    for candidate in candidates:
        ratio = SequenceMatcher(None, target, candidate).ratio()
        if ratio > best_ratio:
            best, best_ratio = candidate, ratio
    return best if best_ratio >= FUZZY_THRESHOLD else target


def display_name(canonical: str) -> str:
    """Title-case a canonical name for the UI ('bicep curl' -> 'Bicep Curl')."""
    return ' '.join(w.capitalize() for w in canonical.split(' ') if w)
