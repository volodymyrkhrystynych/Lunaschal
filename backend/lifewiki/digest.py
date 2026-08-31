"""Everything the user recorded in a window, as one bounded block of text.

Pure with respect to the model — it reads the database (and the notebook
directory) and formats, nothing else — so the whole of what the nightly pass is
shown can be asserted in a test without a single generation. Same split and same
reasoning as `backend/ai/briefing.py`'s `gather_briefing_context` /
`build_briefing_prompt`.

**Every line carries the id of the row it came from**, as `[journal:01J…]`. That
is not decoration: a life fact is required to cite its source, `rebuild_article`
re-derives from those citations, and a model that was never shown an id cannot
produce one. It is the single most load-bearing detail in this module.

Bounded per source rather than in total, so one talkative day of chat cannot
crowd out a week of workouts.
"""
import os
from datetime import datetime
from pathlib import Path

from backend.day_boundary import day_bounds, day_key_for

# Per-source ceilings. Deliberately small: the pass reads this for every article
# it writes, on a local model, inside a window it shares with the briefing.
MAX_JOURNAL = 20
MAX_MESSAGES = 40
MAX_FOOD = 20
MAX_WORKOUTS = 15
MAX_CALENDAR = 25
MAX_NOTEBOOK = 10
MAX_ITEM_CHARS = 600


def _clip(text: str, limit: int = MAX_ITEM_CHARS) -> str:
    text = ' '.join((text or '').split())
    return text if len(text) <= limit else text[:limit].rstrip() + '…'


def _when(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime('%a %d %b %H:%M')


def window_bounds(end_day: str, days: int) -> tuple[int, int]:
    """[start, end) for the `days` 4am-days ending with `end_day` inclusive."""
    from datetime import date, timedelta

    last = date.fromisoformat(end_day)
    first = last - timedelta(days=max(days, 1) - 1)
    return day_bounds(first.isoformat())[0], day_bounds(last.isoformat())[1]


def gather(end_day: str | None = None, days: int = 1, db=None) -> dict:
    """Read the window. Returns {'sources': {name: [line, ...]}, ...}."""
    from backend.db.connection import get_db

    db = db or get_db()
    end_day = end_day or day_key_for()
    start, end = window_bounds(end_day, days)
    sources: dict[str, list[str]] = {}

    rows = db.execute(
        'SELECT id, title, content, tags, created_at FROM journal_entries'
        ' WHERE created_at >= ? AND created_at < ? ORDER BY created_at LIMIT ?',
        (start, end, MAX_JOURNAL),
    ).fetchall()
    sources['journal'] = [
        f'[journal:{r["id"]}] {_when(r["created_at"])}'
        + (f' — {r["title"]}' if r['title'] else '')
        + f'\n{_clip(r["content"])}'
        for r in rows
    ]

    # User messages only. The assistant's own replies are the least reliable
    # thing in the database to build a standing fact from — it would be learning
    # from itself, which is the shortest path to a confident invention.
    rows = db.execute(
        "SELECT id, content, created_at FROM messages"
        " WHERE role='user' AND content != '' AND created_at >= ? AND created_at < ?"
        ' ORDER BY created_at LIMIT ?',
        (start, end, MAX_MESSAGES),
    ).fetchall()
    sources['chat'] = [
        f'[message:{r["id"]}] {_when(r["created_at"])}\n{_clip(r["content"])}'
        for r in rows
    ]

    rows = db.execute(
        'SELECT id, dish, place, notes, rating, created_at FROM food_entries'
        ' WHERE created_at >= ? AND created_at < ? ORDER BY created_at LIMIT ?',
        (start, end, MAX_FOOD),
    ).fetchall()
    food = []
    for r in rows:
        bits = [r['dish'] or 'a meal']
        if r['place']:
            bits.append(f'at {r["place"]}')
        if r['rating']:
            bits.append(f'rated {r["rating"]}/5')
        line = f'[food:{r["id"]}] {_when(r["created_at"])} — {", ".join(bits)}'
        if r['notes']:
            line += f'\n{_clip(r["notes"])}'
        food.append(line)
    sources['food'] = food

    from backend.lifestyle.activity import ACTIVITY_LABELS
    rows = db.execute(
        'SELECT id, date, location_type, duration_minutes, intensity_rating, notes'
        ' FROM workout_sessions WHERE date >= ? AND date <= ? ORDER BY date LIMIT ?',
        (day_key_for(start), end_day, MAX_WORKOUTS),
    ).fetchall()
    workouts = []
    for r in rows:
        bits = [ACTIVITY_LABELS.get(r['location_type'], r['location_type'])]
        if r['duration_minutes']:
            bits.append(f'{r["duration_minutes"]} min')
        if r['intensity_rating']:
            bits.append(f'intensity {r["intensity_rating"]}/5')
        line = f'[workout:{r["id"]}] {r["date"]} — {", ".join(bits)}'
        if r['notes']:
            line += f'\n{_clip(r["notes"])}'
        workouts.append(line)
    sources['workouts'] = workouts

    from backend.calendar_query import events_in_range
    events = events_in_range(db, day_key_for(start), end_day)[:MAX_CALENDAR]
    calendar = []
    for e in events:
        when = e.get('time') or 'all day'
        if e.get('end_time'):
            when += f'–{e["end_time"]}'
        desc = f' — {_clip(e["description"], 200)}' if e.get('description') else ''
        calendar.append(f'[calendar:{e["id"]}] {e["date"]} {when}: {e["title"]}{desc}')
    sources['calendar'] = calendar

    sources['notebook'] = _notebook_notes(start, end)

    from backend import observations
    sources['observations'] = [
        f'[observation:{o["id"]}] {o["content"]}' for o in observations.pending()
    ]

    return {'endDay': end_day, 'days': days, 'start': start, 'end': end,
            'sources': sources}


def _notebook_notes(start: int, end: int) -> list[str]:
    """Notebook files touched in the window, excluding `diary/`.

    Diary notes are promoted into journal entries by
    backend/notebook_diary_scheduler.py once their day is over, so including
    them here would show the same writing twice and let one day's thought be
    counted as two pieces of evidence for the same fact.
    """
    from backend.routes.notebook import NOTEBOOK_DEFAULT_ROOT, NOTEBOOK_ROOT_ENV

    root = Path(os.environ.get(NOTEBOOK_ROOT_ENV, NOTEBOOK_DEFAULT_ROOT)).expanduser()
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.rglob('*.md')):
        if 'diary' in path.relative_to(root).parts:
            continue
        try:
            stat = path.stat()
            if not (start <= int(stat.st_mtime) < end):
                continue
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        if not text.strip():
            continue
        rel = path.relative_to(root).as_posix()
        out.append(f'[notebook:{rel}] {_clip(text)}')
        if len(out) >= MAX_NOTEBOOK:
            break
    return out


def for_sources(citations: list[tuple[str, str]], db=None) -> dict:
    """A digest built from named source rows rather than from a time window.

    What `rebuild_article` re-derives from. The facts of an article cite the rows
    they came from, so re-reading exactly those rows is a re-derivation from
    ground truth — the verification step the drift research asks for, and the
    thing that makes "the wiki is a derived cache" true rather than aspirational.

    It cannot discover a fact the article never had, and is not meant to: a
    rebuild corrects what the machine did to what it read, not what it read.
    """
    from backend.db.connection import get_db

    db = db or get_db()
    wanted: dict[str, list[str]] = {}
    for kind, source_id in citations:
        wanted.setdefault(kind, []).append(source_id)

    sources: dict[str, list[str]] = {}

    def _fetch(sql: str, ids: list[str]):
        placeholders = ','.join('?' * len(ids))
        return db.execute(sql.format(placeholders=placeholders), ids).fetchall()

    if wanted.get('journal'):
        rows = _fetch(
            'SELECT id, title, content, created_at FROM journal_entries'
            ' WHERE id IN ({placeholders}) ORDER BY created_at',
            wanted['journal'],
        )
        sources['journal'] = [
            f'[journal:{r["id"]}] {_when(r["created_at"])}'
            + (f' — {r["title"]}' if r['title'] else '')
            + f'\n{_clip(r["content"])}'
            for r in rows
        ]

    if wanted.get('message'):
        rows = _fetch(
            'SELECT id, content, created_at FROM messages'
            ' WHERE id IN ({placeholders}) ORDER BY created_at',
            wanted['message'],
        )
        sources['chat'] = [
            f'[message:{r["id"]}] {_when(r["created_at"])}\n{_clip(r["content"])}'
            for r in rows
        ]

    if wanted.get('food'):
        rows = _fetch(
            'SELECT id, dish, place, notes, rating, created_at FROM food_entries'
            ' WHERE id IN ({placeholders}) ORDER BY created_at',
            wanted['food'],
        )
        sources['food'] = [
            f'[food:{r["id"]}] {_when(r["created_at"])} — '
            f'{r["dish"] or "a meal"}'
            + (f' at {r["place"]}' if r['place'] else '')
            + (f'\n{_clip(r["notes"])}' if r['notes'] else '')
            for r in rows
        ]

    if wanted.get('workout'):
        from backend.lifestyle.activity import ACTIVITY_LABELS
        rows = _fetch(
            'SELECT id, date, location_type, duration_minutes, intensity_rating,'
            ' notes FROM workout_sessions WHERE id IN ({placeholders}) ORDER BY date',
            wanted['workout'],
        )
        sources['workouts'] = [
            f'[workout:{r["id"]}] {r["date"]} — '
            f'{ACTIVITY_LABELS.get(r["location_type"], r["location_type"])}'
            + (f', {r["duration_minutes"]} min' if r['duration_minutes'] else '')
            + (f'\n{_clip(r["notes"])}' if r['notes'] else '')
            for r in rows
        ]

    if wanted.get('calendar'):
        rows = _fetch(
            'SELECT id, title, description, date, time FROM calendar_events'
            ' WHERE id IN ({placeholders}) ORDER BY date',
            wanted['calendar'],
        )
        sources['calendar'] = [
            f'[calendar:{r["id"]}] {r["date"]} {r["time"] or "all day"}: {r["title"]}'
            + (f' — {_clip(r["description"], 200)}' if r['description'] else '')
            for r in rows
        ]

    # Observations are folded and gone by the time a rebuild runs; a fact citing
    # one has no row left to re-read. Dropping it is correct — a rebuild keeps
    # only what it can still verify.
    return {'endDay': None, 'days': 0, 'start': None, 'end': None,
            'sources': sources}


_LABELS = {
    'journal': 'Journal entries',
    'chat': 'What they said in chat',
    'food': 'Meals logged',
    'workouts': 'Workouts logged',
    'calendar': 'Calendar',
    'notebook': 'Notebook',
    'observations': 'Notes you made for yourself during conversations',
}


def render(digest: dict) -> str:
    """The digest as the block the model is shown. '' when nothing happened."""
    sections = []
    for name, label in _LABELS.items():
        items = digest['sources'].get(name) or []
        if items:
            sections.append(f'## {label}\n\n' + '\n\n'.join(items))
    if not sections:
        return ''
    if not digest.get('endDay'):
        # A rebuild's digest: named rows, no window to describe.
        return ('Everything the user recorded that this article was built '
                'from:\n\n' + '\n\n'.join(sections))
    span = (f'the day of {digest["endDay"]}' if digest['days'] == 1
            else f'the {digest["days"]} days ending {digest["endDay"]}')
    return f'Everything the user recorded over {span}:\n\n' + '\n\n'.join(sections)


def is_empty(digest: dict) -> bool:
    return not any(digest['sources'].values())
