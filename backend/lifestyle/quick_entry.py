"""Deterministic single-set capture. No inference or guessed measurements."""
import re

from backend.lifestyle.exercises import canonicalize

OUTDOOR = {'walk': 'walking', 'walking': 'walking', 'cycle': 'cycling',
           'cycling': 'cycling', 'bike': 'cycling', 'biking': 'cycling',
           'on the bike': 'cycling'}


def parse_entry(text, selected=None, known=()):
    if not isinstance(text, str) or not text.strip() or len(text) > 500:
        raise ValueError('Enter one exercise and its reps or duration.')
    match = re.fullmatch(r'([^\d]*?)(\d.*)', text.strip())
    if not match:
        raise ValueError('Add reps, weight and reps, or minutes.')
    label, numbers = match.groups()
    if label.rstrip().endswith(('-', '+', '.')):
        raise ValueError('Use a positive rep count or duration.')
    name = label.strip() or selected
    if not isinstance(name, str) or not name.strip():
        raise ValueError('Name an exercise or select a recent exercise first.')
    name = name.strip().lower()
    outdoor = OUTDOOR.get(name)
    if outdoor:
        duration = re.fullmatch(r'(\d+)\s*(?:m|min|mins|minute|minutes)?', numbers, re.I)
        if not duration or not 1 <= int(duration[1]) <= 1440:
            raise ValueError('Walking and cycling take one duration in minutes (1–1440).')
        return dict(name=outdoor, raw_name=name, kind='outdoor', duration=int(duration[1]), weight=None, reps=None)
    pair = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(?:lb|lbs|pounds)?\s*[,x×]\s*(\d+)\s*(?:reps?)?', numbers, re.I)
    single = re.fullmatch(r'(\d+)\s*(?:reps?)?', numbers, re.I)
    if not pair and not single:
        raise ValueError('Use weight, reps (20, 10) or bodyweight reps (10).')
    weight = float(pair[1]) if pair else None
    reps = int(pair[2] if pair else single[1])
    if not 1 <= reps <= 10000 or (weight is not None and not 0 <= weight <= 10000):
        raise ValueError('Reps must be 1–10000 and weight 0–10000 lb.')
    canonical = canonicalize(name, known)
    if not canonical:
        raise ValueError('Enter an exercise name.')
    return dict(name=canonical, raw_name=name, kind='strength', duration=None, weight=weight, reps=reps)
