import { describe, expect, it } from 'vitest';
import type { CodeTask, CodeTaskEvent } from '../hooks/api';
import {
  appendEvents,
  clip,
  canPush,
  eventLabel,
  isDetail,
  lineTotals,
  parseDiff,
  pollIntervalMs,
  statusLabel,
  terminalLines,
  appendDictation,
  activity,
  elapsedLabel,
  testVerdict,
} from './codeTasks';

const task = (status: string, extra: Partial<CodeTask> = {}): CodeTask => ({
  id: 'T',
  repoId: 'R',
  title: 't',
  prompt: 'p',
  harness: 'pi',
  status,
  phase: 'build',
  plan: null,
  summary: null,
  diffStat: null,
  headSha: null,
  testExit: null,
  testOutput: null,
  prUrl: null,
  error: null,
  createdAt: null,
  updatedAt: null,
  pending: null,
  running: false,
  ...extra,
});

const ev = (kind: string, payload: Record<string, unknown>): CodeTaskEvent => ({
  seq: 1,
  kind,
  payload,
  createdAt: null,
});

describe('codeTasks', () => {
  // apple/LunaschalCore's CodeAgentTests.testStatusLabelsMatchTheWeb pins the same words.
  it('labels statuses the way the phone does', () => {
    expect(
      [
        'queued',
        'planning',
        'awaiting_plan',
        'running',
        'needs_input',
        'testing',
        'done',
        'interrupted',
        'error',
        'cancelled',
        'pushed',
        'discarded',
      ].map(statusLabel)
    ).toEqual([
      'Queued',
      'Planning',
      'Plan ready',
      'Working',
      'Has a question',
      'Running tests',
      'Ready to review',
      'Interrupted',
      'Failed',
      'Cancelled',
      'Pushed',
      'Discarded',
    ]);
  });

  it('labels model calls with what was prefilled and what was cached', () => {
    expect(
      eventLabel(
        ev('proxy', {
          action: 'call',
          ran: 4.1,
          prefilled: 1200,
          cached: 148000,
        })
      )
    ).toBe('Model: 4s · 1.2k new, 148k cached');
    expect(
      eventLabel(
        ev('proxy', { action: 'call', ran: 3, prefilled: 812, cached: 0 })
      )
    ).toBe('Model: 3s · 812 new');
  });

  it('labels the lookups the agent made through the host', () => {
    const look = (p: Record<string, unknown>) =>
      eventLabel(ev('proxy', { action: 'lookup', ...p }));
    expect(
      look({
        tool: 'library_search',
        arg: 'flask streaming',
        ok: true,
        count: 5,
      })
    ).toBe('Library: 5 results for \u2018flask streaming\u2019');
    expect(look({ tool: 'library_read', arg: 'A/Streaming', ok: true })).toBe(
      'Read from the library: A/Streaming'
    );
    expect(
      look({ tool: 'web_research', arg: 'q', ok: false, blocked: true })
    ).toBe('Web research refused: search the library first');
    expect(
      look({
        tool: 'web_research',
        arg: 'q',
        ok: true,
        count: 1,
        seconds: 41.6,
      })
    ).toBe('Web research: 1 source for \u2018q\u2019 in 42s');
    expect(
      look({ tool: 'repo_wiki_search', arg: 'proxy', ok: true, count: 2 })
    ).toBe('Wiki: 2 notes for \u2018proxy\u2019');
    expect(look({ tool: 'code_map', arg: 'slot', ok: true })).toBe(
      'Code map: \u2018slot\u2019'
    );
  });

  it('labels events the way the phone does', () => {
    expect(
      eventLabel(ev('proxy', { action: 'call', waited: 4.6, ran: 38.2 }))
    ).toBe('Model: 38s (waited 5s for the GPU)');
    expect(eventLabel(ev('proxy', { action: 'promoted' }))).toBe(
      'Rescued a tool call from its thinking'
    );
    expect(
      eventLabel(ev('permission', { title: 'Edit a.py', decision: 'refused' }))
    ).toBe('Refused: Edit a.py');
    expect(eventLabel(ev('test', { exit: 0 }))).toBe('Tests passed');
    expect(eventLabel(ev('test', { exit: 2 }))).toBe('Tests failed (exit 2)');
    expect(eventLabel(ev('status', { status: 'done' }))).toBe(
      'Status: Ready to review'
    );
    expect(isDetail(ev('proxy', {}))).toBe(true);
    expect(isDetail(ev('message', {}))).toBe(false);
  });

  it('polls fast only while the agent works', () => {
    expect(pollIntervalMs([task('awaiting_plan'), task('done')])).toBe(20000);
    expect(pollIntervalMs([task('running')])).toBe(3000);
    expect(pollIntervalMs(undefined)).toBe(20000);
  });

  it('pushes only finished work that was imported', () => {
    expect(canPush(task('done'))).toBe(false);
    expect(canPush(task('done', { headSha: 'abc' }))).toBe(true);
    expect(canPush(task('running', { headSha: 'abc' }))).toBe(false);
  });

  it('totals lines, treating binary files as zero', () => {
    expect(
      lineTotals(
        task('done', {
          diffStat: [
            { path: 'a', added: 3, removed: 1 },
            { path: 'b.png', added: null, removed: null },
          ],
        })
      )
    ).toEqual({ added: 3, removed: 1 });
  });

  it('parses a unified diff into numbered lines', () => {
    const files = parseDiff(
      [
        'diff --git a/backend/a.py b/backend/a.py',
        'index 1111111..2222222 100644',
        '--- a/backend/a.py',
        '+++ b/backend/a.py',
        '@@ -10,3 +10,4 @@ def streak():',
        '     days = load()',
        '-    return len(days)',
        '+    today = day_key_for(now())',
        '+    return len(days) + 1',
        'diff --git a/new.py b/new.py',
        'new file mode 100644',
        '--- /dev/null',
        '+++ b/new.py',
        '@@ -0,0 +1,1 @@',
        '+x = 1',
        'diff --git a/logo.png b/logo.png',
        'Binary files a/logo.png and b/logo.png differ',
        '',
      ].join('\n')
    );
    expect(files.map(f => f.path)).toEqual([
      'backend/a.py',
      'new.py',
      'logo.png',
    ]);
    const lines = files[0].hunks[0].lines;
    expect(lines[0]).toEqual({
      kind: 'context',
      text: '    days = load()',
      oldNumber: 10,
      newNumber: 10,
    });
    expect(lines[1]).toEqual({
      kind: 'removed',
      text: '    return len(days)',
      oldNumber: 11,
      newNumber: null,
    });
    expect(lines.slice(2).map(l => l.newNumber)).toEqual([11, 12]);
    expect(files[0].oldPath).toBeNull();
    expect(files[1].isNew).toBe(true);
    expect(files[2].isBinary).toBe(true);
    expect(parseDiff('')).toEqual([]);
  });
});

const tev = (
  seq: number,
  kind: string,
  payload: Record<string, unknown>
): CodeTaskEvent => ({ seq, kind, payload, createdAt: null });

describe('terminalLines', () => {
  // The same cases as CodeAgentTests.swift, so the two transcripts agree.
  it('reads like a session', () => {
    const lines = terminalLines([
      tev(1, 'user', { text: 'Fix the streak' }),
      tev(2, 'status', { status: 'planning' }),
      tev(3, 'thought', { text: '  Look at habits.py first  ' }),
      tev(4, 'tool_call', {
        title: 'Read backend/habits.py',
        status: 'pending',
      }),
      tev(5, 'tool_result', { status: 'in_progress' }),
      tev(6, 'tool_result', { status: 'completed' }),
      tev(7, 'proxy', { action: 'call', ran: 38.2, waited: 0 }),
      tev(8, 'message', { text: '1. Change the loop\n2. Add a test' }),
    ]);
    expect(lines.map(l => l.style)).toEqual([
      'prompt',
      'dim',
      'thought',
      'tool',
      'dim',
      'dim',
      'text',
    ]);
    expect(lines.map(l => l.text)).toEqual([
      '❯ Fix the streak',
      '── planning ──',
      'Look at habits.py first',
      '● Read backend/habits.py',
      '  ⎿ done',
      '  · model: 38s',
      '1. Change the loop\n2. Add a test',
    ]);
  });

  it('shows failures and test output', () => {
    const lines = terminalLines([
      tev(1, 'permission', { title: 'Edit a.py', decision: 'allowed' }),
      tev(2, 'permission', { title: 'Edit a.py', decision: 'refused' }),
      tev(3, 'tool_result', { status: 'failed' }),
      tev(4, 'proxy', { action: 'stuck' }),
      tev(5, 'test', { exit: 1, tail: '1 failed\n' }),
      tev(6, 'status', {
        status: 'error',
        error: 'session/new timed out after 120s',
      }),
      tev(7, 'status', {
        note: 'the plan step changed files; they were reverted',
      }),
      tev(8, 'message', { text: '   ' }),
    ]);
    expect(lines.map(l => l.text)).toEqual([
      '  ⎿ refused: Edit a.py (the plan step is read-only)',
      '  ⎿ failed',
      '  · stuck: tool calls keep landing in its thinking',
      '✗ tests failed (exit 1)',
      '1 failed',
      '✗ session/new timed out after 120s',
      '  ! the plan step changed files; they were reverted',
    ]);
    expect(lines[4].id).toBe('e5-tail');
  });

  it('draws the checklist', () => {
    const lines = terminalLines([
      tev(1, 'plan', {
        entries: [
          { content: 'Read', status: 'completed' },
          { content: 'Edit', status: 'in_progress' },
          { content: 'Test', status: 'pending' },
        ],
      }),
    ]);
    expect(lines.map(l => l.text)).toEqual(['  ☒ Read\n  ◐ Edit\n  ☐ Test']);
  });
});

describe('appendDictation', () => {
  it('adds to the draft', () => {
    expect(appendDictation('', ' fix it ')).toBe('fix it');
    expect(appendDictation('fix it', 'and test it')).toBe('fix it and test it');
    expect(appendDictation('fix it\n', 'and test it')).toBe(
      'fix it\nand test it'
    );
    expect(appendDictation('fix it', '  ')).toBe('fix it');
  });
});

describe('a running task', () => {
  const at = (
    seq: number,
    kind: string,
    payload: Record<string, unknown>,
    createdAt: string | null = null
  ): CodeTaskEvent => ({ seq, kind, payload, createdAt });

  // The follow-up that looked finished: the previous run's red verdict stayed
  // on screen while the agent worked on a fix.
  it('shows no test verdict while the agent is at work', () => {
    expect(testVerdict(task('running', { testExit: 1 }))).toBeNull();
    expect(testVerdict(task('testing', { testExit: 1 }))).toBeNull();
    expect(testVerdict(task('done', { testExit: 1 }))).toBe('failed');
    expect(testVerdict(task('done', { testExit: 0 }))).toBe('passed');
    expect(testVerdict(task('done'))).toBeNull();
  });

  it('says what it is doing and since when, counting only this status', () => {
    const events = [
      at(1, 'status', { status: 'running' }, '2026-11-09T10:00:00+00:00'),
      at(2, 'tool_call', { title: 'old step' }),
      at(3, 'test', { exit: 1 }),
      at(4, 'status', { status: 'done' }),
      at(5, 'user', { text: 'Tests still failed' }),
      at(6, 'status', { status: 'queued' }),
      at(7, 'status', { status: 'running' }, '2026-11-09T11:38:06+00:00'),
      at(8, 'thought', { text: 'hm' }),
      at(9, 'tool_call', {
        title: 'cd /work && .venv/bin/pytest backend/tests -q\nmore',
      }),
    ];
    const a = activity(task('running'), events)!;
    expect(a.headline).toBe('The agent is working');
    expect(a.since).toBe(Date.parse('2026-11-09T11:38:06Z'));
    expect(a.doing).toBe(
      'Running: cd /work && .venv/bin/pytest backend/tests -q'
    );
  });

  it('has nothing to report before the first step, or once finished', () => {
    const started = [at(1, 'status', { status: 'planning' })];
    expect(activity(task('planning'), started)).toEqual({
      headline: 'The agent is planning',
      since: null,
      doing: null,
    });
    expect(activity(task('done'), started)).toBeNull();
  });

  it('names the test suite while Lunaschal runs it', () => {
    const events = [
      at(1, 'tool_call', { title: 'git commit' }),
      at(2, 'status', { status: 'testing' }),
    ];
    expect(activity(task('testing'), events)).toMatchObject({
      headline: 'Running the test suite',
      doing: null,
    });
  });

  it('formats elapsed time', () => {
    expect(elapsedLabel(45_000)).toBe('45s');
    expect(elapsedLabel(185_000)).toBe('3m 05s');
    expect(elapsedLabel(3_720_000)).toBe('1h 02m');
    expect(elapsedLabel(-5)).toBe('0s');
  });
});

describe('appendEvents', () => {
  const ev = (seq: number) => ({
    seq,
    kind: 'status',
    payload: {},
    createdAt: null,
  });
  it('adds only events after the last one held', () => {
    expect(
      appendEvents([ev(1), ev(2)], [ev(2), ev(3)]).map(e => e.seq)
    ).toEqual([1, 2, 3]);
  });
  it('keeps the same array when nothing is new', () => {
    const held = [ev(1)];
    expect(appendEvents(held, [])).toBe(held);
  });
});

describe('clip', () => {
  it('counts Unicode scalars, not UTF-16 units, like the Swift side', () => {
    const emoji = '😀'.repeat(10);
    expect(clip(emoji, 10)).toBe(emoji);
    expect(clip(emoji, 5)).toBe('😀😀😀😀…');
  });
});
