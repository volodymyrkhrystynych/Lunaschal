"""Goals: one overarching goal, and any number per week/month/quarter/year.

A period goal may carry a daily criterion and a reward; those are judged by one
evaluator shared by chat, the UI and the background sweep. Day results are
cached by source content, not timestamps. Edits, deletions and late uploads
invalidate them; repeated checks never mint another reward.

Every change a person makes to a goal leaves a journal entry behind, written as
a dictated entry is (`raw_content` set, polished in the background) and linked
by `journal_entries.goal_id`. That entry is the goal's history — there is no
revisions table. See backend/goals/CLAUDE.md.
"""
import hashlib
import json
import threading
import time
from datetime import date, timedelta

from ulid import ULID

from backend.ai import service as ai_service
from backend.day_boundary import day_bounds, day_key_for
from backend.db.connection import get_db, row_to_dict
from backend.lifestyle.exercises import canonicalize, normalize_exercise
from backend.goals import evidence
from backend.goals.periods import (
    HORIZONS, PERIOD_HORIZONS, goal_period, is_over, label, period_bounds,
)

_lock = threading.RLock()
MAX_DAYS = 366
MAX_DETAILS = 4000


def _text(value, field, limit=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{field} must contain 1–{limit} characters')
    return value.strip()


def _optional_text(data, key, field, limit=1000):
    value = data.get(key)
    return None if value is None else _text(value, field, limit)


def _details(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_DETAILS:
        raise ValueError(f'details must be at most {MAX_DETAILS} characters')
    return value.strip() or None


def _now(acted_at):
    return int(acted_at if acted_at is not None else time.time())


def is_open(goal: dict) -> bool:
    return goal['endedAt'] is None and goal['status'] != 'cancelled'


def phrase(goal: dict) -> str:
    """How a journal entry names the goal's period: 'for the week of Nov 2'."""
    if goal['horizon'] == 'overarching':
        return 'overarching goal'
    name = label(goal['horizon'], goal['periodStart'])
    return f'goal for the {name[0].lower()}{name[1:]}' if goal['horizon'] == 'week' else f'goal for {name}'


def _journal(goal_id: str, text: str, acted_at: int, entry_id: str | None = None) -> None:
    # Imported here: backend.routes.journal pulls in most of the backend.
    from backend.routes.journal import create_journal_entry
    create_journal_entry(text, text, acted_at, entry_id=entry_id or str(ULID()),
                         polish=True, goal_id=goal_id)


def _rule_line(goal: dict) -> str:
    if not goal['criteria']:
        return ''
    line = f"\n\nDaily rule: {goal['criteria']} ({goal['targetDays']} days in a row)"
    return line + (f" — reward: {goal['reward']}." if goal['reward'] else '.')


def create(data: dict, *, acted_at: int | None = None) -> dict:
    goal_id = data.get('id')
    if goal_id is not None and (not isinstance(goal_id, str) or not goal_id.strip()):
        raise ValueError('id must be a non-empty string')
    if goal_id and get(goal_id):
        return get(goal_id)  # An offline replay of a create that already landed.
    horizon = data.get('horizon')
    if horizon == 'overarching':
        text = data.get('details') or data.get('title')
        return set_overarching(_text(text, 'text', MAX_DETAILS), goal_id=goal_id, acted_at=acted_at)
    if horizon is not None and horizon not in PERIOD_HORIZONS:
        raise ValueError(f'horizon must be one of {", ".join(HORIZONS)}')
    title = _text(data.get('title'), 'title', 200)
    details = _details(data.get('details'))
    criteria = _optional_text(data, 'criteria', 'criteria')
    reward = _optional_text(data, 'reward', 'reward', 300)
    exercise = data.get('exercise') or None
    reps = data.get('minimumReps')
    if exercise:
        exercise = _text(exercise, 'exercise', 200)
        if not normalize_exercise(exercise):
            raise ValueError('exercise needs a usable exercise name')
        if reps is None:
            reps = 1
        if type(reps) is not int or not 1 <= reps <= 100000:
            raise ValueError('minimumReps must be a positive integer')
        # The journal and structured workouts must implement the same rule.
        criteria = f'Complete at least {reps} repetitions of {exercise} during this day.'
    elif reps is not None:
        raise ValueError('minimumReps requires an exercise')
    if reward and not criteria:
        raise ValueError('a reward needs a daily criterion to earn it')
    now = _now(acted_at)
    start = data.get('startDate') or day_key_for(now)
    try:
        parsed = date.fromisoformat(start)
        if parsed.isoformat() != start:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError('startDate must be YYYY-MM-DD')
    today = date.fromisoformat(day_key_for())
    if parsed < today - timedelta(days=MAX_DAYS - 1) or parsed > today + timedelta(days=MAX_DAYS):
        raise ValueError('startDate must be within one year of today')
    days = data.get('targetDays')
    if criteria:
        if horizon is None:
            # The pre-horizon API: a streak goal is filed by its length.
            from backend.goals.periods import horizon_for_days
            horizon = horizon_for_days(days if type(days) is int else None)
        if days is None:
            days = (date.fromisoformat(period_bounds(horizon, start)[1]) - parsed).days + 1
        if type(days) is not int or not 1 <= days <= MAX_DAYS:
            raise ValueError('targetDays must be an integer between 1 and 366')
    else:
        horizon = horizon or 'week'
        days = None
    period_start, period_end = goal_period(horizon, start, days)
    with _lock:
        db = get_db()
        existing = db.execute(
            'SELECT id FROM goals WHERE horizon=? AND title=? AND criteria IS ? AND reward IS ? '
            "AND start_date=? AND target_days IS ? AND status != 'cancelled' AND ended_at IS NULL",
            (horizon, title, criteria, reward, start, days)).fetchone()
        if existing:
            return get(existing['id'])
        goal_id = goal_id or str(ULID())
        db.execute('INSERT INTO goals (id,horizon,title,details,criteria,reward,start_date,target_days,'
                   'period_start,period_end,exercise,minimum_reps,created_at,updated_at) '
                   'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (goal_id, horizon, title, details, criteria, reward, start, days,
                    period_start, period_end, exercise, reps, now, now))
        db.commit()
        goal = get(goal_id)
        text = f"New {phrase(goal)}: {title}" + (f"\n\n{details}" if details else '') + _rule_line(goal)
        # The goal's own id: replaying the create cannot write a second entry.
        _journal(goal_id, text, now, entry_id=goal_id)
    return goal


def overarching() -> dict | None:
    row = get_db().execute(
        "SELECT id FROM goals WHERE horizon='overarching' AND ended_at IS NULL "
        "AND status != 'cancelled'").fetchone()
    return get(row['id']) if row else None


def set_overarching(text: str, *, goal_id: str | None = None, acted_at: int | None = None) -> dict:
    """Replace the one overarching goal. The old one is ended, not edited, so
    each version keeps its own journal entry and its own dates."""
    text = _text(text, 'text', MAX_DETAILS)
    with _lock:
        if goal_id and get(goal_id):
            return get(goal_id)
        previous = overarching()
        if previous and previous['details'] == text:
            return previous
        now = _now(acted_at)
        db = get_db()
        if previous:
            db.execute("UPDATE goals SET status='ended', ended_at=?, updated_at=? WHERE id=?",
                       (now, now, previous['id']))
        goal_id = goal_id or str(ULID())
        db.execute("INSERT INTO goals (id,horizon,title,details,start_date,created_at,updated_at) "
                   "VALUES (?,'overarching',?,?,?,?,?)",
                   (goal_id, text.splitlines()[0][:200], text, day_key_for(now), now, now))
        db.commit()
        entry = f'New overarching goal:\n\n{text}'
        if previous:
            entry += f"\n\nIt replaces:\n\n{previous['details']}"
        _journal(goal_id, entry, now, entry_id=goal_id)
        return get(goal_id)


def edit(goal_id: str, data: dict, *, acted_at: int | None = None) -> dict:
    """Rename or re-describe a goal. Its rule and reward are fixed once set —
    changing what counts after the fact is how a reward gets gamed."""
    with _lock:
        goal = get(goal_id)
        if goal is None:
            raise LookupError('Goal not found')
        if goal['horizon'] == 'overarching':
            return set_overarching(data.get('details') or data.get('title'), acted_at=acted_at)
        if not is_open(goal):
            raise ValueError('This goal has ended; set a new one instead')
        for field in ('criteria', 'reward', 'targetDays', 'startDate', 'horizon', 'exercise', 'minimumReps'):
            if field in data and data[field] != goal.get(field):
                raise ValueError(f'{field} cannot change once a goal is set; end it and set a new one')
        title = _text(data['title'], 'title', 200) if 'title' in data else goal['title']
        details = _details(data['details']) if 'details' in data else goal['details']
        if title == goal['title'] and details == goal['details']:
            return goal
        now = _now(acted_at)
        db = get_db()
        db.execute('UPDATE goals SET title=?, details=?, updated_at=? WHERE id=?',
                   (title, details, now, goal_id))
        db.commit()
        entry = f"Changed a {phrase(goal)}: {title}"
        if title != goal['title']:
            entry += f" (was: {goal['title']})"
        if details != goal['details'] and details:
            entry += f'\n\n{details}'
        _journal(goal_id, entry, now)
        return get(goal_id)


def streaks(days: list[str], today: str) -> tuple[int, int]:
    """Current streak tolerates unfinished today; best proves a past win too."""
    known = set(days)
    current_day = date.fromisoformat(today)
    if today not in known:
        current_day -= timedelta(days=1)
    current = 0
    while current_day.isoformat() in known:
        current += 1
        current_day -= timedelta(days=1)
    best = run = 0
    previous = None
    for key in sorted(known):
        day = date.fromisoformat(key)
        run = run + 1 if previous and day - previous == timedelta(days=1) else 1
        best = max(best, run)
        previous = day
    return current, best


def get(goal_id: str) -> dict | None:
    db = get_db()
    row = db.execute('SELECT * FROM goals WHERE id=?', (goal_id,)).fetchone()
    if row is None:
        return None
    goal = row_to_dict(row)
    rows = db.execute('SELECT * FROM goal_days WHERE goal_id=? ORDER BY date', (goal_id,)).fetchall()
    goal['days'] = [{
        'date': r['date'], 'verdict': r['verdict'],
        'evidence': json.loads(r['evidence']), 'error': r['error'],
    } for r in rows]
    goal['currentStreak'], goal['bestStreak'] = streaks(
        [r['date'] for r in rows if r['verdict'] == 'met' and r['date'] <= day_key_for()], day_key_for())
    goal['needsReview'] = bool(goal['earnedAt'] and goal['targetDays']
                               and goal['bestStreak'] < goal['targetDays'])
    goal['hasRule'] = bool(goal['criteria'])
    goal['periodLabel'] = label(goal['horizon'], goal['periodStart'])
    goal['overdue'] = is_open(goal) and is_over(goal['periodEnd'], day_key_for())
    return goal


def list_goals() -> list[dict]:
    return [get(r['id']) for r in get_db().execute('SELECT id FROM goals ORDER BY created_at DESC, id DESC').fetchall()]


def current(today: str | None = None) -> dict:
    """The overarching goal and, per horizon, the goals still open.

    A goal whose period has passed but has not been graded yet is still listed
    (flagged `overdue`) — it has not gone anywhere until the review closes it.
    `unset` means nothing is open for the period today falls in, which is the
    prompt to set one.
    """
    today = today or day_key_for()
    db = get_db()
    horizons = []
    for horizon in PERIOD_HORIZONS:
        start, end = period_bounds(horizon, today)
        rows = db.execute(
            "SELECT id FROM goals WHERE horizon=? AND ended_at IS NULL AND status != 'cancelled' "
            'AND period_start <= ? ORDER BY period_start, created_at, id', (horizon, today)).fetchall()
        goals = [get(r['id']) for r in rows]
        horizons.append({
            'horizon': horizon, 'label': label(horizon, start),
            'periodStart': start, 'periodEnd': end, 'goals': goals,
            'unset': not any(g['periodEnd'] >= today for g in goals),
        })
    return {'today': today, 'overarching': overarching(), 'horizons': horizons}


def history(goal_id: str) -> list[dict]:
    if get(goal_id) is None:
        raise LookupError('Goal not found')
    rows = get_db().execute(
        'SELECT id, content, raw_content, created_at FROM journal_entries '
        'WHERE goal_id=? ORDER BY created_at DESC, rowid DESC', (goal_id,)).fetchall()
    return [row_to_dict(r) for r in rows]


def _sources(goal, day):
    db = get_db()
    start, end = day_bounds(day)
    # A goal's own journal entries ("New goal: do pushups") are about the goal,
    # never evidence of doing it.
    journal = [{'id': r['id'], 'text': r['content'] or r['raw_content'] or ''} for r in db.execute(
        'SELECT id,content,raw_content FROM journal_entries WHERE created_at>=? AND created_at<? '
        'AND goal_id IS NULL ORDER BY id', (start,end)).fetchall()]
    workouts = [dict(r) for r in db.execute(
        'SELECT w.id, e.name_canonical, s.reps FROM workout_sessions w '
        'JOIN workout_exercises e ON e.session_id=w.id JOIN workout_sets s ON s.exercise_id=e.id '
        'WHERE w.date=? ORDER BY w.id,e.id,s.id', (day,)).fetchall()] if goal['exercise'] else []
    return journal, workouts


def evaluate(goal_id: str, *, retry=True) -> dict:
    with _lock:
        goal = get(goal_id)
        if goal is None:
            raise LookupError('Goal not found')
        if goal['status'] == 'cancelled' or not goal['hasRule']:
            return goal
        db = get_db()
        today = date.fromisoformat(day_key_for())
        day = date.fromisoformat(goal['startDate'])
        # Until the review closes a goal it stays judgeable, even past its
        # period; once ended, nothing after that day can count.
        end = today
        if goal['endedAt'] is not None:
            ended = get_db().execute('SELECT ended_at FROM goals WHERE id=?', (goal_id,)).fetchone()[0]
            end = min(today, date.fromisoformat(day_key_for(ended)))
        snapshots = {}
        while day <= end:
            key = day.isoformat()
            journal, workouts = _sources(goal, key)
            fingerprint = hashlib.sha256(json.dumps([journal,workouts],sort_keys=True).encode()).hexdigest()
            snapshots[key] = (journal, workouts)
            cached = db.execute('SELECT * FROM goal_days WHERE goal_id=? AND date=?', (goal_id,key)).fetchone()
            now = int(time.time())
            if cached and cached['fingerprint'] == fingerprint:
                if (cached['verdict'] != 'uncertain' or
                        (not retry and (not cached['error'] or now - cached['checked_at'] < 600))):
                    day += timedelta(days=1)
                    continue
            verdict, citations, error = 'not_met', [], None
            if goal['exercise']:
                target = normalize_exercise(goal['exercise'])
                matched = [w for w in workouts if canonicalize(w['name_canonical'], [target]) == target]
                if sum(w['reps'] or 0 for w in matched) >= goal['minimumReps']:
                    verdict = 'met'
                    citations = [{'kind': 'workout', 'id': w['id'],
                                  'text': f"{w['name_canonical']}: {w['reps']} reps"} for w in matched]
            if verdict != 'met' and journal:
                try:
                    ai_service.clear_deferral()
                    if sum(len(e['text']) for e in journal) > 24000:
                        raise RuntimeError('Too much journal text for one check; narrow the daily record.')
                    verdict, citations = evidence.judge(goal['criteria'], key, journal)
                except Exception as exc:
                    verdict, error = 'uncertain', str(exc)[:300]
            # A source may change while the model is running. Never award from
            # that stale snapshot; the next check will retry the changed day.
            if _sources(goal, key) != (journal, workouts):
                verdict, citations, error = 'uncertain', [], 'Sources changed during this check. Check again.'
            db.execute('INSERT INTO goal_days (id,goal_id,date,fingerprint,verdict,evidence,error,checked_at) '
                       'VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(goal_id,date) DO UPDATE SET '
                       'fingerprint=excluded.fingerprint,verdict=excluded.verdict,evidence=excluded.evidence,'
                       'error=excluded.error,checked_at=excluded.checked_at',
                       (str(ULID()),goal_id,key,fingerprint,verdict,json.dumps(citations),error,now))
            db.commit()
            if error and ai_service.deferred_reason():
                # Release the goal lock promptly when a foreground chat
                # preempts the background sweep. Never award from a partial
                # scan; the next run resumes using the durable day cache.
                return get(goal_id) | {'checkIncomplete': True}
            day += timedelta(days=1)
        # A slow last-day interpretation must not award against an earlier
        # day's evidence that was edited while it was running.
        for key, snapshot in snapshots.items():
            if _sources(goal, key) != snapshot:
                db.execute("UPDATE goal_days SET verdict='uncertain',evidence='[]',fingerprint='',error=? "
                           'WHERE goal_id=? AND date=?',
                           ('Sources changed during this check. Check again.',goal_id,key))
        db.commit()
        result = get(goal_id)
        won = result['bestStreak'] >= result['targetDays']
        # Keep the original award receipt even if a later correction requires
        # review. A redeemed prize cannot be silently "unspent".
        unwon = 'needs_review' if result['earnedAt'] else ('ended' if result['endedAt'] else 'active')
        status = 'redeemed' if result['status'] == 'redeemed' else ('earned' if won else unwon)
        now = int(time.time())
        db.execute('UPDATE goals SET status=?, earned_at=CASE WHEN ? THEN COALESCE(earned_at,?) ELSE earned_at END, '
                   'last_checked_at=?,updated_at=? WHERE id=?', (status,won,now,now,now,goal_id))
        db.commit()
        return get(goal_id)


def change_status(goal_id: str, action: str, *, acted_at: int | None = None) -> dict:
    if action not in ('cancel', 'redeem', 'end'):
        raise ValueError('action must be cancel, redeem or end')
    with _lock:
        goal = get(goal_id)
        if not goal:
            raise LookupError('Goal not found')
        if action == 'redeem':
            goal = evaluate(goal_id)
            if goal.get('checkIncomplete'):
                raise ValueError('Evidence check was interrupted. Check again before redeeming.')
            if goal['status'] == 'redeemed':
                return goal
            if goal['status'] != 'earned' or goal['needsReview']:
                raise ValueError('The reward has not been verified as earned')
        elif goal['status'] == 'redeemed' and action == 'cancel':
            raise ValueError('A redeemed reward cannot be cancelled')
        elif not is_open(goal):
            return goal
        now = _now(acted_at)
        db = get_db()
        if action == 'end':
            # The reward state survives the period closing; only a goal still
            # in progress becomes plain `ended`.
            db.execute("UPDATE goals SET status=CASE WHEN status='active' THEN 'ended' ELSE status END, "
                       'ended_at=?, updated_at=? WHERE id=?', (now, now, goal_id))
            entry = f"Ended the {phrase(goal)}: {goal['title']}"
        elif action == 'cancel':
            db.execute("UPDATE goals SET status='cancelled', updated_at=? WHERE id=?", (now, goal_id))
            entry = f"Dropped the {phrase(goal)}: {goal['title']}"
        else:
            db.execute("UPDATE goals SET status='redeemed', redeemed_at=COALESCE(redeemed_at,?), "
                       'updated_at=? WHERE id=?', (now, now, goal_id))
            entry = f"Redeemed the reward for {goal['title']}: {goal['reward']}"
        db.commit()
        _journal(goal_id, entry, now)
        return get(goal_id)


def _goal_line(goal: dict) -> str:
    line = f"- {goal['title']}"
    if goal['details']:
        line += f" — {goal['details'][:300]}"
    if goal['hasRule']:
        line += f" [daily rule: {goal['criteria']}; streak {goal['currentStreak']}/{goal['targetDays']}"
        line += f"; reward {goal['reward']} ({goal['status']})]" if goal['reward'] else ']'
    if goal['overdue']:
        line += ' (period over, not yet reviewed)'
    return line


def context_text(today: str | None = None) -> str:
    """The goals as the briefing and every chat prompt read them. Empty when
    nothing at all is set, so a prompt carries no empty heading."""
    data = current(today)
    lines = []
    if data['overarching']:
        lines += ['Overarching goal:', data['overarching']['details'] or data['overarching']['title']]
    for h in data['horizons']:
        if h['goals']:
            lines.append(f"This {h['horizon']} ({h['label']}):")
            lines += [_goal_line(g) for g in h['goals']]
        elif lines or any(x['goals'] for x in data['horizons']):
            lines.append(f"This {h['horizon']} ({h['label']}): no goals set yet.")
    return '\n'.join(lines)
