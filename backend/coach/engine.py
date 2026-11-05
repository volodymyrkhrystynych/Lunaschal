"""The reminder engine: plan the day's reminders, fire the due ones, and score
them once their window has closed. Driven by `scheduler.tick` every minute
while `coach_enabled` is on.

One row in `coach_decisions` per reminder considered:

1. **plan** — after 4am, each behaviour the user tracks gets one decision for
   the day, at the slot the bandit samples (`bandit.choose_slot`). Behaviours
   are the weigh-in, calories, each habit, and setting goals for a period
   that has none.
2. **fire** — at its slot, `policy.blocked` may skip it (done already, quiet
   hours, the day's cap, a habit that has stuck); otherwise the bandit picks a
   framing or holds it back (`bandit.choose_arm`), and a sent one goes out
   through `deliver` — a chat message, then a push.
3. **resolve** — when its window closes, `reward` is whether the thing got
   done (`rewards.py`), for held decisions as much as sent ones.

What has been learned is never stored: `bandit.posteriors` recomputes it from
these rows each time, so there is no model state to drift or corrupt.
"""
import json
import random
import time
from datetime import date, datetime, timedelta

from ulid import ULID

from backend.coach import bandit, compose, config, features, policy, rewards
from backend.day_boundary import day_bounds, day_key_for
from backend.db.connection import get_db

CATEGORIES = {'weight': 'COACH_WEIGHT', 'calories': 'COACH_CALORIES', 'habit': 'COACH_HABIT',
              'goals': 'COACH_GOALS'}
EXCUSING = ('external', 'one_off')


def _rng(rng):
    return rng if rng is not None else random.Random()


def targets(db, day: str) -> list[str]:
    """The behaviours that may be reminded about on `day`."""
    from backend.goals.service import current
    wanted = config.behaviors(db)
    out = []
    if wanted['weight']:
        out.append('weight')
    if wanted['calories']:
        out.append('calories')
    if wanted['habits']:
        out += [f"habit:{r['id']}" for r in db.execute('SELECT id FROM daily_tasks ORDER BY position')]
    out += [f"goals:{h['horizon']}" for h in current(day)['horizons'] if h['unset']]
    return out


def done_times(db, behavior: str, day: str) -> list[int]:
    """When the thing a behaviour is about was done on `day`."""
    start, end = day_bounds(day)
    family, _, key = behavior.partition(':')
    if family == 'weight':
        rows = db.execute('SELECT created_at FROM body_weight_logs WHERE date=?', (day,))
    elif family == 'calories':
        rows = db.execute('SELECT created_at FROM calorie_logs WHERE date=? UNION ALL '
                          'SELECT created_at FROM food_entries WHERE created_at>=? AND created_at<?',
                          (day, start, end))
    elif family == 'habit':
        rows = db.execute('SELECT created_at FROM daily_task_completions WHERE task_id=? AND date=?', (key, day))
    elif family == 'goals':
        rows = db.execute('SELECT created_at FROM goals WHERE horizon=? AND created_at>=? AND created_at<?',
                          (key, start, end))
    else:
        return []
    return [r[0] for r in rows]


def _decisions(db, since: str | None = None) -> list[dict]:
    sql, args = 'SELECT * FROM coach_decisions', ()
    if since:
        sql, args = sql + ' WHERE day>=?', (since,)
    return [dict(r) for r in db.execute(sql, args)]


def learned(db, today: str) -> dict:
    since = (date.fromisoformat(today) - timedelta(days=180)).isoformat()
    return bandit.posteriors(_decisions(db, since), today)


def plan_day(now: int | None = None, rng=None) -> int:
    """One decision per behaviour for today, at a sampled slot still ahead."""
    now = int(now if now is not None else time.time())
    rng = _rng(rng)
    db = get_db()
    day = day_key_for(now)
    post = learned(db, day)
    planned = {r['behavior'] for r in db.execute('SELECT behavior FROM coach_decisions WHERE day=?', (day,))}
    made = 0
    for behavior in targets(db, day):
        if behavior in planned:
            continue
        ahead = [s for s in policy.slots(behavior) if policy.slot_time(day, s) > now]
        if not ahead:
            continue
        slot = bandit.choose_slot(post.get(behavior), ahead, rng)
        db.execute('INSERT OR IGNORE INTO coach_decisions (id, behavior, day, slot, planned_for, created_at) '
                   'VALUES (?,?,?,?,?,?)', (str(ULID()), behavior, day, slot, policy.slot_time(day, slot), now))
        made += 1
    db.commit()
    return made


def _facts(db, behavior: str, day: str) -> dict:
    from backend.goals.service import overarching
    from backend.habit_streaks import current_streak
    goal = overarching()
    facts = {'goal': goal['title'] if goal else None}
    family, _, key = behavior.partition(':')
    if family == 'habit':
        row = db.execute('SELECT title, cue FROM daily_tasks WHERE id=?', (key,)).fetchone()
        done = [r[0] for r in db.execute('SELECT date FROM daily_task_completions WHERE task_id=?', (key,))]
        facts |= {'title': row['title'] if row else None, 'cue': row['cue'] if row else None,
                  'streak': current_streak(done, day)}
    elif family == 'weight':
        days = [r[0] for r in db.execute('SELECT date FROM body_weight_logs')]
        facts['streak'] = current_streak(days, day)
    elif family == 'calories':
        days = [r[0] for r in db.execute('SELECT DISTINCT date FROM calorie_logs')]
        facts['streak'] = current_streak(days, day)
    return facts


def _done_days(db, behavior: str, day: str) -> list[str]:
    start = date.fromisoformat(day)
    return [(start - timedelta(days=i)).isoformat() for i in range(1, 15)
            if done_times(db, behavior, (start - timedelta(days=i)).isoformat())]


def _unprompted_run(db, behavior: str, day: str) -> int:
    """Days running, up to yesterday, the thing was done with no reminder sent."""
    sent = {r[0] for r in db.execute("SELECT day FROM coach_decisions WHERE behavior=? AND status='sent'",
                                     (behavior,))}
    run, cursor = 0, date.fromisoformat(day) - timedelta(days=1)
    while run < 30 and cursor.isoformat() not in sent and done_times(db, behavior, cursor.isoformat()):
        run += 1
        cursor -= timedelta(days=1)
    return run


def fire_due(now: int | None = None, rng=None, client=None) -> list[dict]:
    """Decide every planned reminder whose slot has come today."""
    from backend.coach.deliver import deliver
    now = int(now if now is not None else time.time())
    rng = _rng(rng)
    db = get_db()
    day = day_key_for(now)
    settings = config.engine(db)
    post = learned(db, day)
    due = db.execute("SELECT * FROM coach_decisions WHERE status='planned' AND day=? AND planned_for<=? "
                     'ORDER BY planned_for, id', (day, now)).fetchall()
    decided = []
    for row in due:
        behavior = row['behavior']
        sent_rows = db.execute("SELECT decided_at FROM coach_decisions WHERE status='sent' AND decided_at>=?",
                               (now - 7 * 86400,)).fetchall()
        sent_today = db.execute("SELECT COUNT(*) FROM coach_decisions WHERE status='sent' AND day=?",
                                (day,)).fetchone()[0]
        done_days = _done_days(db, behavior, day)
        context = features.context(db, behavior=behavior, day=day, now=now, sent_today=sent_today,
                                   burden=policy.burden([r[0] for r in sent_rows], now), done_days=done_days)
        status = policy.blocked(
            behavior=behavior, hour=datetime.fromtimestamp(now).hour,
            quiet=(settings['quiet_start'], settings['quiet_end']), sent_today=sent_today,
            cap=settings['daily_cap'], done=any(t <= now for t in done_times(db, behavior, day)),
            unprompted_run=_unprompted_run(db, behavior, day) if behavior.startswith('habit:') else 0, rng=rng)
        update = {'status': status, 'context': json.dumps(context), 'decided_at': now}
        if status is None:
            arm, mode = bandit.choose_arm(post.get(behavior), rng, settings['no_send_rate'])
            update |= {'arm': arm, 'mode': mode}
            if arm == bandit.NO_SEND:
                update['status'] = 'held'
            else:
                family, _, key = behavior.partition(':')
                title, body, _ = compose.compose(behavior, arm, _facts(db, behavior, day))
                coach = {'kind': family, 'day': day, 'decisionId': row['id'], 'behavior': behavior}
                data = {'decisionId': row['id']}
                if family == 'habit':
                    coach['habitId'] = data['habitId'] = key
                if family == 'goals':
                    data['horizons'] = [key]
                sent = deliver(f'**{title}** — {body}', {'coach': coach}, title=title, body=body,
                               category=CATEGORIES[family], data=data, collapse_id=f'{behavior}-{day}',
                               now=now, client=client)
                update |= {'status': 'sent', 'title': title, 'body': body, 'message_id': sent['messageId'],
                           'pushed': sent['pushed']}
        db.execute(f"UPDATE coach_decisions SET {', '.join(f'{k}=?' for k in update)} WHERE id=?",
                   (*update.values(), row['id']))
        db.commit()
        decided.append({'id': row['id'], 'behavior': behavior, **update})
    return decided


def _excused(db, behavior: str, day: str) -> bool:
    family, _, key = behavior.partition(':')
    rows = db.execute("SELECT target_kind, target_id FROM day_excuses WHERE day=? AND kind IN ('external','one_off')",
                      (day,)).fetchall()
    for r in rows:
        if r['target_kind'] == 'day' or (r['target_kind'] == family and family in ('weight', 'calories')) \
                or (r['target_kind'] == 'habit' and family == 'habit' and r['target_id'] == key):
            return True
    return False


def resolve(now: int | None = None) -> int:
    """Score every sent or held decision whose window has closed."""
    now = int(now if now is not None else time.time())
    db = get_db()
    rows = db.execute("SELECT * FROM coach_decisions WHERE status IN ('sent','held') AND resolved_at IS NULL "
                      'AND planned_for<=?', (now,)).fetchall()
    scored = 0
    for row in rows:
        _, day_end = day_bounds(row['day'])
        end = rewards.window_end(row['behavior'], row['planned_for'], day_end)
        if now < end:
            continue
        reward = rewards.outcome(done_times(db, row['behavior'], row['day']), row['planned_for'], end)
        db.execute('UPDATE coach_decisions SET reward=?, excused=?, resolved_at=? WHERE id=?',
                   (reward, int(_excused(db, row['behavior'], row['day'])), now, row['id']))
        scored += 1
    db.commit()
    return scored


RESPONSES = ('opened', 'done', 'snooze', 'dismissed')


def respond(decision_id: str, action: str, at: int | None = None) -> dict:
    """What the user did with a reminder. Recorded as context; the reward is
    still whether the thing got done, so a dismissal is not a zero."""
    if action not in RESPONSES:
        raise ValueError(f'action must be one of {", ".join(RESPONSES)}')
    db = get_db()
    row = db.execute('SELECT id FROM coach_decisions WHERE id=?', (decision_id,)).fetchone()
    if row is None:
        raise LookupError('No such reminder')
    db.execute('UPDATE coach_decisions SET response=?, responded_at=? WHERE id=?',
               (action, int(at if at is not None else time.time()), decision_id))
    db.commit()
    return {'id': decision_id, 'response': action}


def _label(db, behavior: str) -> str:
    family, _, key = behavior.partition(':')
    if family == 'habit':
        row = db.execute('SELECT title FROM daily_tasks WHERE id=?', (key,)).fetchone()
        return row['title'] if row else 'A removed habit'
    if family == 'goals':
        return f'Setting goals for the {key}'
    return {'weight': 'Weigh-in', 'calories': 'Calories'}[family]


def stats(today: str | None = None) -> list[dict]:
    """Per behaviour: how often it got done with and without a reminder, and
    what the bandit currently believes about each framing and slot."""
    db = get_db()
    today = today or day_key_for()
    post = learned(db, today)
    rows = _decisions(db)
    out = []
    for behavior in sorted({r['behavior'] for r in rows}):
        mine = [r for r in rows if r['behavior'] == behavior]
        resolved = [r for r in mine if r['reward'] is not None and not r['excused']]
        sent = [r for r in resolved if r['status'] == 'sent']
        held = [r for r in resolved if r['status'] == 'held']
        learned_here = post.get(behavior, {'arms': {}, 'slots': {}})
        out.append({
            'behavior': behavior, 'label': _label(db, behavior),
            'sent': sum(1 for r in mine if r['status'] == 'sent'),
            'held': sum(1 for r in mine if r['status'] == 'held'),
            'skipped': sum(1 for r in mine if r['status'].startswith('skipped')),
            'doneWhenSent': round(sum(r['reward'] for r in sent) / len(sent), 3) if sent else None,
            'doneWhenHeld': round(sum(r['reward'] for r in held) / len(held), 3) if held else None,
            'arms': {a: {'mean': round(b.mean, 3), 'n': round(b.n, 1)} for a, b in learned_here['arms'].items()},
            'slots': {s: {'mean': round(b.mean, 3), 'n': round(b.n, 1)} for s, b in learned_here['slots'].items()},
        })
    return out


CONFIDENT_WIDTH = 0.3
MIN_EVIDENCE = 8


def context_text(today: str | None = None) -> str:
    """What the coach has learned, once it has learned something, for the
    chat prompt and the briefing — so the agent can say it, not guess it."""
    db = get_db()
    today = today or day_key_for()
    lines = []
    for behavior, learned_here in learned(db, today).items():
        arms = {a: b for a, b in learned_here['arms'].items() if a != bandit.NO_SEND and b.n >= MIN_EVIDENCE}
        if not arms:
            continue
        arm, belief = max(arms.items(), key=lambda kv: kv[1].mean)
        if belief.width() > CONFIDENT_WIDTH:
            continue
        line = f"- {_label(db, behavior)}: '{arm}' reminders work best (done ~{belief.mean:.0%} of the time)"
        none = learned_here['arms'].get(bandit.NO_SEND)
        if none and none.n >= MIN_EVIDENCE:
            line += f"; without a reminder ~{none.mean:.0%}"
        slots = {s: b for s, b in learned_here['slots'].items() if b.n >= MIN_EVIDENCE / 2}
        if slots:
            line += f"; best around {max(slots.items(), key=lambda kv: kv[1].mean)[0]}"
        lines.append(line)
    if not lines:
        return ''
    return 'What reminders have learned about the user:\n' + '\n'.join(lines)
