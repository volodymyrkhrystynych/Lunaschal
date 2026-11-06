"""Chat tools for the day review: explain a day, read it, nudge its rating,
and give a habit its cue.

`day` is an enum — today or yesterday — resolved here to a 4am day key, so the
model never does date arithmetic across the 4am boundary.
"""
import json

from backend.coach import review
from backend.day_boundary import day_key_for
from backend.db.connection import get_db


def _tool(name, description, properties, required=()):
    return {'type': 'function', 'function': {'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties,
                       'required': list(required), 'additionalProperties': False}}}


DAY = {'type': 'string', 'enum': ['today', 'yesterday']}

TOOLS = [
    _tool('record_excuse', 'Record why something the user tracks did not happen, when they tell you '
          '(e.g. "I was slammed at work yesterday, didn\'t weigh in"). kind: external = outside their '
          'control (illness, travel, a work crunch); one_off = an unusual day; internal = they chose not '
          'to or forgot. External and one-off reasons excuse the item: it leaves the day\'s rating and '
          'the habit\'s streak is kept. Internal reasons are kept as an obstacle to plan around, never '
          'judged. targetKind day excuses the whole day. Record only what they actually said.', {
        'day': DAY,
        'targetKind': {'type': 'string', 'enum': list(review.TARGET_KINDS)},
        'target': {'type': 'string', 'description': 'The habit, goal or to-do title; omit for day/weight/calories'},
        'reason': {'type': 'string', 'description': 'Their reason, close to their words'},
        'kind': {'type': 'string', 'enum': list(review.EXCUSE_KINDS)},
        'obstacle': {'type': 'string', 'description': 'A short name for what got in the way, e.g. "late meetings"'},
    }, ['day', 'targetKind', 'reason', 'kind']),
    _tool('get_day_review', 'Read the review of today (so far) or yesterday: what was done, missed '
          'and excused, and the rating. The rating is computed; never make one up.', {'day': DAY}, ['day']),
    _tool('adjust_day_rating', 'Move a day\'s computed rating by at most one star, only when the user '
          'gives context the records cannot show. Always give the reason.', {
        'day': DAY, 'rating': {'type': 'integer', 'minimum': 1, 'maximum': 5}, 'reason': {'type': 'string'},
    }, ['day', 'rating', 'reason']),
    _tool('set_habit_cue', 'Attach an if-then cue to a habit when the user agrees to one '
          '("after coffee" for "weigh in"). An empty cue clears it.', {
        'habit': {'type': 'string'}, 'cue': {'type': 'string'},
    }, ['habit', 'cue']),
]
TOOL_NAMES = {t['function']['name'] for t in TOOLS}

DECISION_NOTE = (
    "\n\nWhen the user explains why something didn't happen — a busy day, being ill, forgetting — "
    'call record_excuse before answering, so the review and their streaks reflect it. Be kind about '
    'it: an excuse is context, not a confession. Use get_day_review for how a day went, '
    'adjust_day_rating only with a reason they gave, and set_habit_cue when they agree to an if-then plan.')


def _day(value: str) -> str:
    if value == 'today':
        return day_key_for()
    if value == 'yesterday':
        return review.yesterday()
    raise ValueError('day must be today or yesterday')


def _compact(r: dict) -> dict:
    return {k: r[k] for k in ('day', 'status', 'rating', 'computedRating', 'overrideReason',
                              'done', 'missed', 'excused', 'summary')} | {
        'items': [{'kind': i['kind'], 'title': i['title'],
                   'status': 'excused' if i['excused'] else 'unknown' if i['done'] is None
                             else 'done' if i['done'] else 'missed',
                   'reason': i.get('excuse')} for i in r['items']]}


def run_tool(name, args):
    event = {'tool': name, 'arg': json.dumps(args), 'ok': True}
    try:
        if name == 'record_excuse':
            excuse, updated = review.record_excuse(
                _day(args.get('day')), args.get('targetKind'), args.get('target'),
                args.get('reason'), args.get('kind'), args.get('obstacle'))
            event['title'] = excuse['target_title']
            return json.dumps({'recorded': excuse['target_title'], 'excused': excuse['kind'] != 'internal',
                               'review': _compact(updated)}), event
        if name == 'get_day_review':
            day = _day(args.get('day'))
            result = review.get(day) if args.get('day') == 'yesterday' and review.get(day) else review.draft(day)
            return json.dumps(_compact(result)), event
        if name == 'adjust_day_rating':
            return json.dumps(_compact(review.override(_day(args.get('day')), args.get('rating'),
                                                       args.get('reason')))), event
        if name == 'set_habit_cue':
            title = args.get('habit')
            task_id, title = review.resolve_target(get_db(), day_key_for(), 'habit', title)
            set_cue(task_id, args.get('cue'))
            event['title'] = title
            return json.dumps({'habit': title, 'cue': (args.get('cue') or '').strip() or None}), event
        raise ValueError('Unknown coach tool')
    except (ValueError, LookupError) as exc:
        event.update(ok=False, error=str(exc))
        return str(exc), event


def set_cue(task_id: str, cue: str | None) -> None:
    import time
    db = get_db()
    cue = (cue or '').strip()[:200] or None
    if not db.execute('UPDATE daily_tasks SET cue=?, updated_at=? WHERE id=?',
                      (cue, int(time.time()), task_id)).rowcount:
        raise LookupError('Habit not found')
    db.commit()
