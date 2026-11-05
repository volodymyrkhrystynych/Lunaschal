import { describe, expect, it } from 'vitest';
import type { CodeTask, CodeTaskEvent } from '../hooks/api';
import {
  canPush,
  eventLabel,
  isDetail,
  lineTotals,
  parseDiff,
  pollIntervalMs,
  statusLabel,
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
