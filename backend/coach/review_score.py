"""How a day is scored. Pure and DB-free.

The rating is arithmetic, never the model's: done ÷ (counted − excused),
mapped onto 1–5. A model asked to rate a day it is also being kind about drifts
toward kindness, and a rating that can be talked up isn't feedback. The agent
may move the result by one star, with a stored reason (`clamp_override`) —
that is how "it was a busy day" can change a rating without the number
becoming negotiable.

An item is one thing the day was supposed to hold: a habit, the weigh-in, a
calorie log, a criteria goal's verdict, a to-do on the day's bar. `done` is
True, False, or None for "can't say" (a goal the judge was unsure about),
which is left out of the count the way the Goals feature already treats it.
"""
from collections import Counter
from datetime import date, timedelta

EXCUSING = ('external', 'one_off')
RECURRING_WINDOW = 14
RECURRING_THRESHOLD = 3


def _matches(excuse: dict, item: dict) -> bool:
    if excuse['target_kind'] == 'day':
        return True
    if excuse['target_kind'] != item['kind']:
        return False
    return item['kind'] in ('weight', 'calories') or excuse.get('target_id') == item.get('id')


def apply_excuses(items: list[dict], excuses: list[dict]) -> list[dict]:
    """Each item with `excused` and the reason it was given, if any. An
    internal reason ("didn't feel like it") is recorded on the item but does
    not excuse it — it's an obstacle to plan around, not a pass."""
    out = []
    for item in items:
        item = dict(item, excused=False, excuse=None)
        for excuse in excuses:
            if _matches(excuse, item):
                item['excuse'] = excuse['reason']
                if excuse['kind'] in EXCUSING:
                    item['excused'] = True
        out.append(item)
    return out


def rating_for(score: float | None) -> int | None:
    if score is None:
        return None
    if score >= 0.9:
        return 5
    if score >= 0.7:
        return 4
    if score >= 0.5:
        return 3
    if score >= 0.25:
        return 2
    return 1


def score(items: list[dict]) -> dict:
    counted = [i for i in items if i['done'] is not None and not i.get('excused')]
    done = sum(1 for i in counted if i['done'])
    value = done / len(counted) if counted else None
    return {
        'score': value, 'rating': rating_for(value), 'done': done, 'counted': len(counted),
        'excused': sum(1 for i in items if i.get('excused')),
    }


def clamp_override(computed: int | None, wanted: int) -> int:
    """An override is at most one star from the arithmetic."""
    if computed is None:
        raise ValueError('There is nothing to rate on that day')
    if not isinstance(wanted, int) or not 1 <= wanted <= 5:
        raise ValueError('rating must be 1–5')
    if abs(wanted - computed) > 1:
        raise ValueError(f'The rating can move one star from the computed {computed}, not to {wanted}')
    return wanted


def _obstacle_key(excuse: dict) -> str:
    return ' '.join((excuse.get('obstacle') or excuse['reason']).lower().split())


def recurring(excuses: list[dict], today: str, window: int = RECURRING_WINDOW,
              threshold: int = RECURRING_THRESHOLD) -> list[dict]:
    """Obstacles that keep coming back: the same target and obstacle on
    `threshold` or more distinct days in the last `window`. Those are what
    the review proposes an if-then plan for (WOOP's "obstacle → plan") rather
    than excusing again."""
    since = (date.fromisoformat(today) - timedelta(days=window - 1)).isoformat()
    days: dict[tuple, set[str]] = {}
    sample: dict[tuple, dict] = {}
    for e in excuses:
        if not since <= e['day'] <= today:
            continue
        key = (e['target_kind'], e.get('target_id'), _obstacle_key(e))
        days.setdefault(key, set()).add(e['day'])
        sample[key] = e
    out = []
    for key, seen in days.items():
        if len(seen) >= threshold:
            e = sample[key]
            out.append({'targetKind': key[0], 'targetId': key[1], 'targetTitle': e.get('target_title'),
                        'obstacle': e.get('obstacle') or e['reason'], 'days': len(seen)})
    return sorted(out, key=lambda r: -r['days'])


def stars(rating: int | None) -> str:
    return '' if rating is None else '★' * rating + '☆' * (5 - rating)


def tally(items: list[dict]) -> Counter:
    return Counter('excused' if i.get('excused') else 'unknown' if i['done'] is None
                   else 'done' if i['done'] else 'missed' for i in items)
