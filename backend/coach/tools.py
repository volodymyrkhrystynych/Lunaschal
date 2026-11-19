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
    _tool('end_workday', 'Call when the user says they are done with work for the day ("done for the '
          'day", "finished work", "logging off"), asks to play or unlock games, or answers the message '
          'saying games were closed. Call it again on every such message, even when an earlier card is '
          'still showing: it re-reads the day, so habits done since then count. Drafts today\'s review and '
          'returns the rating today will get if nothing else changes, with what is still open. When games '
          'are locked it also puts an Unlock gaming card in front of them; nothing unlocks until they '
          'accept it.', {}),
]
TOOL_NAMES = {t['function']['name'] for t in TOOLS}

DECISION_NOTE = (
    "\n\nWhen the user explains why something didn't happen — a busy day, being ill, forgetting — "
    'call record_excuse before answering, so the review and their streaks reflect it. Be kind about '
    'it: an excuse is context, not a confession. Use get_day_review for how a day went, '
    'adjust_day_rating only with a reason they gave, and set_habit_cue when they agree to an if-then plan. '
    'When they say they are done with work for the day, ask to play or unlock games, or reply to the '
    'message saying games were closed, call end_workday before answering — every time, even if you '
    'called it earlier today.')


def replies_to_lock_message(conversation_id: str | None, now: int | None = None) -> bool:
    """Whether the newest user message answers today's "I closed Steam"
    message: the assistant message just before it is the enforcer's
    announcement for this 4am day. Structural, not a reading of the words —
    that message asks for exactly one thing, to be told when work is done.

    Ordered by id, not `created_at`: a whole exchange fits inside a second,
    and the ULID is the tie-break that reflects the real order.
    """
    if not conversation_id:
        return False
    db = get_db()
    user = db.execute("SELECT id FROM messages WHERE conversation_id=? AND role='user'"
                      ' ORDER BY id DESC LIMIT 1', (conversation_id,)).fetchone()
    if user is None:
        return False
    before = db.execute("SELECT metadata FROM messages WHERE conversation_id=? AND role='assistant'"
                        ' AND id < ? ORDER BY id DESC LIMIT 1', (conversation_id, user['id'])).fetchone()
    try:
        coach = json.loads(before['metadata'] or '{}').get('coach') or {} if before else {}
    except (TypeError, ValueError, AttributeError):
        return False
    return coach.get('kind') == 'gaming_lock' and coach.get('day') == day_key_for(now)


def _waiting_unlock_card(db, conversation_id: str | None, day: str) -> bool:
    """Whether an unlock card for `day` is still pending in this chat."""
    if not conversation_id:
        return False
    for row in db.execute("SELECT metadata FROM messages WHERE conversation_id=? AND role='assistant'"
                          " AND metadata LIKE '%unlock_gaming%'", (conversation_id,)):
        try:
            proposals = json.loads(row['metadata']).get('proposals') or []
        except (TypeError, ValueError, AttributeError):
            continue
        if any(isinstance(p, dict) and p.get('kind') == 'unlock_gaming' and p.get('status') == 'pending'
               and (p.get('data') or {}).get('day') == day for p in proposals):
            return True
    return False


def answer_note(proposals: list[dict], conversation_id: str | None = None,
                now: int | None = None) -> str:
    """What the reply has to say about the gaming lock this turn.

    Measured against the live model (scripts/chat_eval.py), two things went
    wrong. With a card staged, the reply reviewed the day and never said the
    card was there — the tool description's "point at the card" was the one
    instruction it reliably dropped. And on a follow-up ("did the habits, now
    unlock"), the decision turn sometimes skipped end_workday and the reply
    said "Games unlocked." with nothing unlocked. No prompt makes the tool call
    certain, so the reply turn — the one writing the text — is told the truth
    either way, from the lock's own state.
    """
    card = next((p for p in proposals if p.get('kind') == 'unlock_gaming'), None)
    if card is None:
        from backend.gaming import lock
        db = get_db()
        state = lock.status(db, now)
        if not state['locked']:
            return ''
        note = ('\n\nGames are still locked: nothing unlocked them this turn, so never say or imply '
                'that games are unlocked. ')
        if _waiting_unlock_card(db, conversation_id, state['day']):
            return note + ('An Unlock gaming card from earlier in this chat is still waiting; if they '
                           'want to play, tell them to tap Unlock gaming on it.')
        return note + 'If they want to play, ask them to tell you when work is done.'
    data = card.get('data') or {}
    rating = f"{data['rating']}/5" if data.get('rating') else 'no rating yet'
    still_open = ', '.join(data.get('open') or []) or 'nothing'
    return ('\n\nAn Unlock gaming card is showing under this reply. Today stands at '
            f'{rating}; still open: {still_open}. Say the rating and what is open in a sentence or '
            'two, then tell them to tap Unlock gaming on the card when they are ready. Games stay locked '
            'until they tap it, so do not call them unlocked. You may nudge '
            'toward an easy open item, but the card is theirs to tap now: do not question them about '
            'their day or make unlocking wait on anything, and never say you cannot unlock games.')


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
        if name == 'end_workday':
            return end_workday(event)
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


def end_workday(event: dict, now: int | None = None):
    """Today's provisional review, and — on a locked day — the card that
    lifts the gaming lock. The card carries the rating as it stands, so what
    the user accepts is what they were told."""
    from backend.gaming import lock
    db = get_db()
    day = day_key_for(now)
    today = review.draft(day, now)
    result = {'review': _compact(today),
              'openItems': [i['title'] for i in today['items'] if i['done'] is False and not i['excused']]}
    state = lock.status(db, now)
    result['workEnd'] = state['workEnd']
    if not state['applies']:
        result['gaming'] = 'The gaming lock is not on today; nothing to unlock.'
        return json.dumps(result), event
    if not state['locked']:
        result['gaming'] = 'Games are already unlocked today.'
        return json.dumps(result), event
    result['gaming'] = 'An Unlock gaming card is now in front of the user. Nothing is unlocked until they accept it.'
    event['title'] = 'Unlock gaming'
    event['proposal'] = {'kind': 'unlock_gaming', 'data': {
        'day': day, 'rating': today['rating'], 'done': today['done'], 'missed': today['missed'],
        'open': result['openItems'][:10], 'workEnd': state['workEnd'], 'pauseInference': True}}
    return json.dumps(result), event


def set_cue(task_id: str, cue: str | None) -> None:
    import time
    db = get_db()
    cue = (cue or '').strip()[:200] or None
    if not db.execute('UPDATE daily_tasks SET cue=?, updated_at=? WHERE id=?',
                      (cue, int(time.time()), task_id)).rowcount:
        raise LookupError('Habit not found')
    db.commit()
