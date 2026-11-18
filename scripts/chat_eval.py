"""Live chat evals: the real chat path against a real llama-server.

The pytest suite mocks the model, so it proves the plumbing and nothing about
what Qwen actually does with a sentence. This drives the same run a phone
message gets (`runs.start`, the 'chat' toolset, the server-built system prompt)
against a seeded demo database and a live model, and checks what came out:
which tools were called, which cards were staged, what accepting them wrote.

Opt-in and slow, never part of `npm run test:backend`. Sampling is not
deterministic, so every scenario runs several times and the report is a pass
rate, with every transcript saved for reading.

    # a llama-server serving the `qwen36` alias, e.g. on a Mac:
    LLAMA_BIN=/opt/homebrew/bin/llama-server \\
      LLAMA_PRESETS=llama/presets.mac.ini ./llama/start-llama.sh &
    .venv/bin/python scripts/chat_eval.py --runs 5 --only gaming

Nothing here touches ./data/lunaschal.db: every path the app or the seeder
writes is pointed under --out before the backend is imported, and each run
gets its own copy of one seeded database.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Roots the seeder insists on, plus the ones test-env.sh adds for the app.
EXTRA_ROOTS = ['JOURNAL_ARCHIVE_ROOT', 'LOCATION_ROOT', 'CANON_ROOT', 'LEARNING_ROOT',
               'TODOS_ROOT', 'STUDY_ARCHIVE_ROOT', 'AGENT_ROOT', 'REPOS_ROOT']


def _seeder_required_vars() -> list[str]:
    """The seeder's own list, read without importing it — importing runs its
    env guard, which exits on exactly the variables this is about to set."""
    import ast
    tree = ast.parse((ROOT / 'scripts' / 'seed_test_db.py').read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', None) == 'REQUIRED_ENV_VARS':
            return ast.literal_eval(node.value)
    raise RuntimeError('REQUIRED_ENV_VARS not found in seed_test_db.py')


def scratch_env(out: Path) -> dict:
    REQUIRED_ENV_VARS = _seeder_required_vars()
    env = {}
    for var in REQUIRED_ENV_VARS + EXTRA_ROOTS:
        if var == 'DATABASE_URL':
            env[var] = str(out / 'template.db')
        elif var == 'SHORTCUTS_PATH':
            env[var] = str(out / 'shortcuts.json')
        else:
            env[var] = str(out / 'roots' / var.lower())
    env['LUNASCHAL_NO_SCHEDULERS'] = '1'
    env['LOCATION_TILE_URL'] = ''
    return env


# ── one turn, one run ────────────────────────────────────────────────────────

@dataclass
class Turn:
    user: str | None
    message_id: str
    reply: str = ''
    steps: list = field(default_factory=list)
    proposals: list = field(default_factory=list)
    status: str = ''
    error: str | None = None
    seconds: float = 0.0

    def tools(self) -> list[str]:
        return [s.get('tool') for s in self.steps]

    def called(self, name: str) -> bool:
        return any(s.get('tool') == name and s.get('ok', True) for s in self.steps)

    def proposal(self, kind: str):
        return next((p for p in self.proposals if p.get('kind') == kind), None)


@dataclass
class Ctx:
    client: object
    conversation_id: str
    turns: list = field(default_factory=list)
    accepts: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def last(self) -> Turn:
        return self.turns[-1]

    def db(self):
        from backend.db.connection import get_db
        return get_db()


def say(text: str):
    def step(ctx: Ctx):
        from backend.chat.autoreply import segment_messages
        from backend.db.connection import get_db
        from backend.delegate import runs
        from ulid import ULID
        r = ctx.client.post(f'/api/chat/conversations/{ctx.conversation_id}/messages',
                            json={'role': 'user', 'content': text})
        assert r.status_code == 201, r.get_data(as_text=True)
        # The phone's ordering: the user row first, then the reply's row, so
        # the ULIDs sort the way a real exchange does.
        message_id = str(ULID())
        db = get_db()
        db.execute("INSERT INTO messages(id, conversation_id, role, content, metadata, status, created_at)"
                   " VALUES (?,?,'assistant','',NULL,'streaming',?)",
                   (message_id, ctx.conversation_id, int(time.time())))
        db.commit()
        # What the phone sends is the segment minus the empty reply row.
        messages = [m for m in segment_messages(ctx.conversation_id) if m['id'] != message_id]
        started = time.monotonic()
        q = runs.start(message_id, messages, '', toolset='chat', conversation_id=ctx.conversation_id)
        while q.get()[0] != '_end':
            pass
        row = db.execute('SELECT content, metadata, status, error FROM messages WHERE id=?',
                         (message_id,)).fetchone()
        meta = json.loads(row['metadata'] or '{}')
        ctx.turns.append(Turn(user=text, message_id=message_id, reply=row['content'] or '',
                              steps=meta.get('steps') or [], proposals=meta.get('proposals') or [],
                              status=row['status'], error=row['error'],
                              seconds=round(time.monotonic() - started, 1)))
    step.label = f'say: {text}'
    return step


def accept(kind: str, data: dict | None = None):
    """Accept the newest pending card of `kind`, as a tap would.

    An unlock card is accepted with its GPU-pause box unticked: ticked, the
    accept POSTs `/models/unload` to the llama-server under test, which the
    next scenario would then wait out a reload for — and which, on a shared
    server, unloads somebody else's model.
    """
    if kind == 'unlock_gaming':
        data = {'pauseInference': False, **(data or {})}

    def step(ctx: Ctx):
        for turn in reversed(ctx.turns):
            p = turn.proposal(kind)
            if p and p.get('status') == 'pending':
                body = {'action': 'accept', **({'data': {**p['data'], **data}} if data else {})}
                r = ctx.client.post(f"/api/chat/proposals/{turn.message_id}/{p['id']}", json=body)
                ctx.accepts.append({'kind': kind, 'status': r.status_code,
                                    'body': r.get_json(silent=True)})
                return
        ctx.accepts.append({'kind': kind, 'status': None, 'body': 'no pending card to accept'})
    step.label = f'accept {kind}'
    return step


def announce_lock():
    """The enforcer's once-a-day message after it closed a launcher."""
    def step(ctx: Ctx):
        from backend.gaming import enforcer
        enforcer.announce(['steam'])
    step.label = 'enforcer closes steam'
    return step


def unlock_today(method='review'):
    def step(ctx: Ctx):
        from backend.gaming import lock
        lock.unlock(ctx.db(), lock.day_key_for(), method, reason='already unlocked earlier today')
    step.label = f'unlock today ({method})'
    return step


# ── checks ───────────────────────────────────────────────────────────────────

CLAIMS_UNLOCKED = re.compile(r"\b(games? ((are|is) )?(now )?unlocked|i(?:'ve| have) unlocked|unlocked (your|the) games"
                             r"|i(?:'m| am) unlocking|(you(?:'re| are)|you're all) clear to play)\b", re.I)


def unlocked_row(ctx: Ctx):
    from backend.gaming import lock
    return lock.unlock_row(ctx.db(), lock.day_key_for())


def gaming_checks(*, turn=-1, expect_card=True, then_accept=True):
    checks = [
        (f'turn {turn}: end_workday called', lambda c: c.turns[turn].called('end_workday')),
    ]
    if expect_card:
        checks.append((f'turn {turn}: unlock_gaming card staged',
                       lambda c: c.turns[turn].proposal('unlock_gaming') is not None))
        checks.append((f'turn {turn}: reply points at the card',
                       lambda c: re.search(r'\b(card|unlock)', c.turns[turn].reply, re.I) is not None))
    checks.append((f'turn {turn}: reply does not claim games are already unlocked',
                   lambda c: not CLAIMS_UNLOCKED.search(c.turns[turn].reply)))
    if then_accept:
        checks += [
            ('accept answered 200', lambda c: bool(c.accepts) and c.accepts[-1]['status'] == 200),
            ('gaming_unlocks row is method=review', lambda c: (unlocked_row(c) or {}).get('method') == 'review'),
        ]
    return checks


def no_false_saves(turn=-1):
    """A reply that says it saved something must have a step that did."""
    pat = re.compile(r"\b(i(?:'ve| have) (added|saved|logged|created|scheduled)|added (it|that|them) to)\b", re.I)
    return (f'turn {turn}: no unbacked "added/saved" claim',
            lambda c: not pat.search(c.turns[turn].reply) or bool(c.turns[turn].steps))


@dataclass
class Scenario:
    name: str
    group: str
    steps: list
    checks: list[tuple[str, Callable]]
    note: str = ''


SCENARIOS = [
    Scenario('done_for_the_day', 'gaming',
             [say("I'm done with work for today."), accept('unlock_gaming')],
             gaming_checks()),
    Scenario('done_then_habits_then_unlock', 'gaming',
             [say("Done with work for the day, can I play some games?"),
              say("Ok, I did the stretching and drank my water. Now unlock the games please."),
              accept('unlock_gaming')],
             [('(info) turn 1: end_workday called again', lambda c: c.turns[1].called('end_workday')),
              ('turn 1: no false "unlocked" claim', lambda c: not CLAIMS_UNLOCKED.search(c.turns[1].reply)),
              ('turn 1: reply points at a card', lambda c: re.search(r'\b(card|unlock)', c.turns[1].reply, re.I) is not None),
              ('accept answered 200', lambda c: bool(c.accepts) and c.accepts[-1]['status'] == 200),
              ('gaming_unlocks row is method=review', lambda c: (unlocked_row(c) or {}).get('method') == 'review')],
             note='The real flow: the coach asks for habits first, then the user asks again. Either a fresh '
                  'card or the earlier one is fine; claiming games are unlocked is not.'),
    Scenario('done_then_habits_then_ok', 'gaming',
             [say("Finished work, logging off."),
              say("Did both of those just now."),
              accept('unlock_gaming')],
             [('a card exists by the last turn', lambda c: any(t.proposal('unlock_gaming') for t in c.turns)),
              ('accept answered 200', lambda c: bool(c.accepts) and c.accepts[-1]['status'] == 200),
              ('gaming_unlocks row is method=review', lambda c: (unlocked_row(c) or {}).get('method') == 'review')],
             note='A terse follow-up after pushback; the first turn\'s card may still be the one accepted.'),
    Scenario('unlock_games_plain', 'gaming',
             [say("Unlock my games please."), accept('unlock_gaming')],
             gaming_checks(),
             note='Asks to unlock without saying work is over.'),
    Scenario('reply_to_enforcer', 'gaming',
             [announce_lock(), say("Yeah I'm done for today, go over it with me."), accept('unlock_gaming')],
             gaming_checks()),
    Scenario('already_unlocked', 'gaming',
             [unlock_today(), say("I'm done with work, unlock games.")],
             [('end_workday called', lambda c: c.last.called('end_workday')),
              ('no card staged', lambda c: c.last.proposal('unlock_gaming') is None)]),

    Scenario('todos_short_titles', 'tools',
             [say("Add to my todos: call the dentist about the crown before Friday, and buy printer ink "
                  "because the office one ran out")],
             [('add_todos called', lambda c: c.last.called('add_todos')),
              ('titles are short labels',
               # The step's arg is the saved titles joined with '; ', not the call's JSON.
               lambda c: all(len(title.split()) <= 8 for s in c.last.steps if s.get('tool') == 'add_todos'
                             for title in s['arg'].split('; '))),
              no_false_saves()]),
    Scenario('calendar_relative_date', 'tools',
             [say("Put a dentist appointment on my calendar for next Tuesday at 3pm, about an hour.")],
             [('calendar card staged', lambda c: c.last.proposal('calendar') is not None),
              ('date is a Tuesday', lambda c: _weekday((c.last.proposal('calendar') or {}).get('data', {}).get('date')) == 1),
              ('time is 15:00', lambda c: (c.last.proposal('calendar') or {}).get('data', {}).get('time') == '15:00')]),
    Scenario('vague_date_asks', 'tools',
             [say("Remind me to renew my passport sometime before the trip.")],
             [('ask_user or undated todo, no invented date',
               lambda c: c.last.called('ask_user') or c.last.called('add_todos'))]),
    Scenario('excuse_missed_habit', 'tools',
             [say("I didn't stretch yesterday, I was sick with a fever all day.")],
             [('record_excuse called', lambda c: c.last.called('record_excuse')),
              ('kind external', lambda c: any('"external"' in s.get('arg', '') for s in c.last.steps
                                               if s.get('tool') == 'record_excuse'))]),
    Scenario('how_was_yesterday', 'tools',
             [say("How did I do yesterday?")],
             [('get_day_review called', lambda c: c.last.called('get_day_review'))]),
    Scenario('meal_vs_calories', 'tools',
             [say("Just had a chicken caesar salad at the cafe downstairs, around 650 calories.")],
             [('food or calorie card staged',
               lambda c: c.last.proposal('food') is not None or c.last.proposal('calorie') is not None)]),
    Scenario('small_talk_no_tools', 'tools',
             [say("Haha thanks, that's all for now.")],
             [('no write tools', lambda c: not set(c.last.tools()) & {
                 'add_todos', 'propose_calendar_event', 'create_note_to_self', 'remember', 'end_workday'})]),
]


def _weekday(day):
    from datetime import date
    try:
        return date.fromisoformat(day).weekday()
    except (TypeError, ValueError):
        return None


# ── running ──────────────────────────────────────────────────────────────────

def run_one(app, template: Path, run_dir: Path, scenario: Scenario, thinking: bool) -> dict:
    from backend.db import connection
    db_path = run_dir / 'run.db'
    shutil.copyfile(template, db_path)
    connection._DB_PATH, connection._conn = str(db_path), None
    db = connection.get_db()
    db.execute('UPDATE settings SET llm_thinking=? WHERE id=1', (int(thinking),))
    db.commit()
    client = app.test_client()
    conv = client.post('/api/chat/conversations', json={}).get_json()['id']
    ctx = Ctx(client=client, conversation_id=conv)
    result = {'scenario': scenario.name, 'checks': {}, 'error': None}
    try:
        for step in scenario.steps:
            step(ctx)
        for name, fn in scenario.checks:
            try:
                result['checks'][name] = bool(fn(ctx))
            except Exception as e:
                result['checks'][name] = False
                ctx.notes.append(f'check {name!r} raised {e!r}')
    except Exception:
        result['error'] = traceback.format_exc()
    result['passed'] = result['error'] is None and all(
        v for k, v in result['checks'].items() if not k.startswith('(info)'))
    result['turns'] = [t.__dict__ for t in ctx.turns]
    result['accepts'] = ctx.accepts
    result['notes'] = ctx.notes
    if connection._conn is not None:
        connection._conn.close()
        connection._conn = None
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--runs', type=int, default=5)
    ap.add_argument('--only', help='a group (gaming, tools) or comma-separated scenario names')
    ap.add_argument('--thinking', action='store_true', help='turn on llm_thinking for P1 chat')
    ap.add_argument('--llama-url', default='http://localhost:8080')
    ap.add_argument('--out', type=Path, default=ROOT / 'data' / 'chat-eval')
    args = ap.parse_args()

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = scratch_env(out)
    os.environ.update(env)
    template = Path(env['DATABASE_URL'])
    if not template.exists():
        print(f'Seeding {template} ...', flush=True)
        subprocess.run([sys.executable, str(ROOT / 'scripts' / 'seed_test_db.py')],
                       env={**os.environ, **env}, check=True, stdout=subprocess.DEVNULL)

    from backend.db import connection
    connection._DB_PATH, connection._conn = str(template), None
    db = connection.get_db()
    db.execute("UPDATE settings SET llama_url=?, llama_model='qwen36', "
               "gaming_lock_days='[0,1,2,3,4,5,6]' WHERE id=1", (args.llama_url,))
    db.commit()
    connection._conn.close()
    connection._conn = None

    from backend.app import create_app
    app = create_app()

    chosen = SCENARIOS
    if args.only:
        wanted = set(args.only.split(','))
        chosen = [s for s in SCENARIOS if s.group in wanted or s.name in wanted]
    stamp = time.strftime('%Y%m%d-%H%M%S')
    report_dir = out / 'reports' / stamp
    report_dir.mkdir(parents=True)
    summary = []
    for scenario in chosen:
        results = []
        for i in range(args.runs):
            run_dir = report_dir / scenario.name / str(i)
            run_dir.mkdir(parents=True)
            r = run_one(app, template, run_dir, scenario, args.thinking)
            (run_dir / 'result.json').write_text(json.dumps(r, indent=2, default=str))
            (run_dir / 'run.db').unlink(missing_ok=True)
            results.append(r)
            failed = [k for k, v in r['checks'].items() if not v]
            tools = ' | '.join(','.join(t['steps'] and [s['tool'] for s in t['steps']] or ['-'])
                               for t in r['turns'])
            print(f"{scenario.name} #{i}: {'PASS' if r['passed'] else 'FAIL'}  tools[{tools}]"
                  + (f'  failed: {failed}' if failed else '') + ('  ERROR' if r['error'] else ''), flush=True)
        passed = sum(r['passed'] for r in results)
        summary.append({'scenario': scenario.name, 'group': scenario.group,
                        'passed': passed, 'runs': len(results),
                        'checks': {k: sum(r['checks'].get(k, False) for r in results)
                                   for k, _ in scenario.checks}})
    (report_dir / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(f'\nReport: {report_dir}')
    for s in summary:
        print(f"  {s['scenario']:32} {s['passed']}/{s['runs']}")
        for k, v in s['checks'].items():
            if v < s['runs']:
                print(f"      {v}/{s['runs']}  {k}")


if __name__ == '__main__':
    main()
