"""One evaluator for chat, the UI, and the background sweep.

Day results are cached by source content, not timestamps. Edits, deletions and
late uploads invalidate them; repeated checks never mint another reward.
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

_lock = threading.RLock()
MAX_DAYS = 366


def _text(value, field, limit=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{field} must contain 1–{limit} characters')
    return value.strip()


def create(data: dict) -> dict:
    title = _text(data.get('title'), 'title', 200)
    criteria = _text(data.get('criteria'), 'criteria')
    reward = _text(data.get('reward'), 'reward', 300)
    days = data.get('targetDays', 7)
    if type(days) is not int or not 1 <= days <= MAX_DAYS:
        raise ValueError('targetDays must be an integer between 1 and 366')
    start = data.get('startDate') or day_key_for()
    try:
        parsed = date.fromisoformat(start)
        if parsed.isoformat() != start:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError('startDate must be YYYY-MM-DD')
    today = date.fromisoformat(day_key_for())
    if parsed < today - timedelta(days=MAX_DAYS - 1) or parsed > today + timedelta(days=MAX_DAYS):
        raise ValueError('startDate must be within one year of today')
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
    now = int(time.time())
    with _lock:
        db = get_db()
        existing = db.execute(
            'SELECT id FROM goals WHERE title=? AND criteria=? AND reward=? AND start_date=? '
            "AND target_days=? AND status != 'cancelled'",
            (title, criteria, reward, start, days)).fetchone()
        if existing:
            return get(existing['id'])
        goal_id = str(ULID())
        db.execute('INSERT INTO goals (id,title,criteria,reward,start_date,target_days,exercise,minimum_reps,created_at,updated_at) '
                   'VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (goal_id,title,criteria,reward,start,days,exercise,reps,now,now))
        db.commit()
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
    goal['needsReview'] = bool(goal['earnedAt'] and goal['bestStreak'] < goal['targetDays'])
    return goal


def list_goals() -> list[dict]:
    return [get(r['id']) for r in get_db().execute('SELECT id FROM goals ORDER BY created_at DESC, id DESC').fetchall()]


def _sources(goal, day):
    db = get_db()
    start, end = day_bounds(day)
    journal = [{'id': r['id'], 'text': r['content'] or r['raw_content'] or ''} for r in db.execute(
        'SELECT id,content,raw_content FROM journal_entries WHERE created_at>=? AND created_at<? ORDER BY id',
        (start,end)).fetchall()]
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
        if goal['status'] == 'cancelled':
            return goal
        db = get_db()
        today = date.fromisoformat(day_key_for())
        day = date.fromisoformat(goal['startDate'])
        end = today
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
        status = 'redeemed' if result['status'] == 'redeemed' else (
            'earned' if won else ('needs_review' if result['earnedAt'] else 'active'))
        now = int(time.time())
        db.execute('UPDATE goals SET status=?, earned_at=CASE WHEN ? THEN COALESCE(earned_at,?) ELSE earned_at END, '
                   'last_checked_at=?,updated_at=? WHERE id=?', (status,won,now,now,now,goal_id))
        db.commit()
        return get(goal_id)


def change_status(goal_id: str, action: str) -> dict:
    if action not in ('cancel', 'redeem'):
        raise ValueError('action must be cancel or redeem')
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
        elif goal['status'] == 'redeemed':
            raise ValueError('A redeemed reward cannot be cancelled')
        now = int(time.time())
        db = get_db()
        db.execute('UPDATE goals SET status=?, redeemed_at=CASE WHEN ? THEN COALESCE(redeemed_at,?) ELSE redeemed_at END, '
                   'updated_at=? WHERE id=?',
                   ('redeemed' if action == 'redeem' else 'cancelled',action == 'redeem',now,now,goal_id))
        db.commit()
        return get(goal_id)
