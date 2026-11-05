"""What one 4am day was supposed to hold, and what of it happened.

Each item is `{kind, id, title, done}` with `done` True/False, or None when
it can't be said (a goal the judge was unsure of). Sources:

- habits: `daily_tasks` that existed by the end of the day, ticked or not
- weight: a `body_weight_logs` row for the day (typed, or projected from
  Apple Health)
- calories: a `calorie_logs` row for the day **or** a meal in `food_entries`
  — a photographed meal is a calorie log in all but name
- goals: a daily-rule goal's `goal_days` verdict for the day
- to-dos: the day's bar (`chat_todos`), the plan the briefing made

Which of the first three count is the user's (`coach_behaviors`). Weight and
calories also count only once the user has logged one at all: a review that
marks down a weigh-in nobody ever took up is noise, not feedback.
"""
from backend.coach.config import behaviors
from backend.day_boundary import day_bounds


def gather(db, day: str) -> list[dict]:
    start, end = day_bounds(day)
    wanted = behaviors(db)
    items = []
    if wanted['habits']:
        for r in db.execute(
            'SELECT t.id, t.title, c.id AS done FROM daily_tasks t '
            'LEFT JOIN daily_task_completions c ON c.task_id=t.id AND c.date=? '
            'WHERE t.created_at < ? ORDER BY t.position', (day, end)
        ):
            items.append({'kind': 'habit', 'id': r['id'], 'title': r['title'], 'done': r['done'] is not None})
    if wanted['weight'] and db.execute('SELECT 1 FROM body_weight_logs WHERE date<=? LIMIT 1', (day,)).fetchone():
        logged = db.execute('SELECT 1 FROM body_weight_logs WHERE date=?', (day,)).fetchone()
        items.append({'kind': 'weight', 'id': None, 'title': 'Weigh-in', 'done': logged is not None})
    if wanted['calories'] and _ever_logged_calories(db, end):
        logged = (db.execute('SELECT 1 FROM calorie_logs WHERE date=? LIMIT 1', (day,)).fetchone()
                  or db.execute('SELECT 1 FROM food_entries WHERE created_at>=? AND created_at<? LIMIT 1',
                                (start, end)).fetchone())
        items.append({'kind': 'calories', 'id': None, 'title': 'Calories logged', 'done': logged is not None})
    for r in db.execute(
        'SELECT g.id, g.title, d.verdict FROM goal_days d JOIN goals g ON g.id=d.goal_id '
        "WHERE d.date=? AND g.status != 'cancelled' ORDER BY g.created_at", (day,)
    ):
        done = {'met': True, 'not_met': False}.get(r['verdict'])
        items.append({'kind': 'goal', 'id': r['id'], 'title': r['title'], 'done': done})
    for r in db.execute('SELECT id, title, done FROM chat_todos WHERE day_key=? ORDER BY created_at, id', (day,)):
        items.append({'kind': 'todo', 'id': r['id'], 'title': r['title'], 'done': bool(r['done'])})
    return items


def _ever_logged_calories(db, before: int) -> bool:
    return bool(db.execute('SELECT 1 FROM calorie_logs WHERE created_at<? LIMIT 1', (before,)).fetchone()
                or db.execute('SELECT 1 FROM food_entries WHERE created_at<? LIMIT 1', (before,)).fetchone())


def excuses(db, day: str | None = None, since: str | None = None) -> list[dict]:
    sql = 'SELECT * FROM day_excuses'
    args: tuple = ()
    if day:
        sql, args = sql + ' WHERE day=?', (day,)
    elif since:
        sql, args = sql + ' WHERE day>=?', (since,)
    return [dict(r) for r in db.execute(sql + ' ORDER BY created_at, id', args)]
