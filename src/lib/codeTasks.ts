import type { CodeTask, CodeTaskEvent } from '../hooks/api';

/**
 * The Code tab's rules, shared word for word with the iPhone's
 * `LunaschalCore/CodeAgent.swift` — `CodeAgentTests.testStatusLabelsMatchTheWeb`
 * and this file's test pin the same strings, so the two clients describe a
 * task the same way.
 */

export const ACTIVE_STATES = new Set([
  'queued',
  'planning',
  'running',
  'testing',
]);
export const CLOSED_STATES = new Set(['cancelled', 'pushed', 'discarded']);
const NEEDS_YOU = new Set([
  'awaiting_plan',
  'needs_input',
  'done',
  'error',
  'interrupted',
]);

const LABELS: Record<string, string> = {
  queued: 'Queued',
  planning: 'Planning',
  awaiting_plan: 'Plan ready',
  running: 'Working',
  needs_input: 'Has a question',
  testing: 'Running tests',
  done: 'Ready to review',
  interrupted: 'Interrupted',
  error: 'Failed',
  cancelled: 'Cancelled',
  pushed: 'Pushed',
  discarded: 'Discarded',
};

export function statusLabel(status: string): string {
  return LABELS[status] ?? status.charAt(0).toUpperCase() + status.slice(1);
}

export const isActive = (t: CodeTask) => ACTIVE_STATES.has(t.status);
export const needsYou = (t: CodeTask) => NEEDS_YOU.has(t.status);
export const canMessage = (t: CodeTask) =>
  ['needs_input', 'done', 'error', 'interrupted', 'awaiting_plan'].includes(
    t.status
  );
export const canPush = (t: CodeTask) =>
  (t.status === 'done' || t.status === 'needs_input') && !!t.headSha;
export const canContinue = (t: CodeTask) =>
  t.status === 'interrupted' || t.status === 'error';
export const canDiscard = (t: CodeTask) =>
  !isActive(t) && !CLOSED_STATES.has(t.status);

/** Fast while the agent works — a phase takes minutes — slow otherwise. */
export function pollIntervalMs(tasks: CodeTask[] | undefined): number {
  return (tasks ?? []).some(isActive) ? 3000 : 20000;
}

export function lineTotals(t: CodeTask): { added: number; removed: number } {
  return (t.diffStat ?? []).reduce(
    (acc, f) => ({
      added: acc.added + (f.added ?? 0),
      removed: acc.removed + (f.removed ?? 0),
    }),
    { added: 0, removed: 0 }
  );
}

const str = (v: unknown): string | undefined =>
  typeof v === 'string' ? v : undefined;
const num = (v: unknown): number | undefined =>
  typeof v === 'number' ? v : undefined;

/** Model calls and tool plumbing: collapsed by default. */
export const isDetail = (e: CodeTaskEvent) =>
  [
    'thought',
    'tool_call',
    'tool_result',
    'permission',
    'proxy',
    'plan',
  ].includes(e.kind);

/** One line for the timeline. */
export function eventLabel(e: CodeTaskEvent): string {
  const p = e.payload;
  switch (e.kind) {
    case 'user':
      return str(p.text) ?? 'You';
    case 'message':
      return str(p.text) ?? '';
    case 'thought':
      return 'Thought it over';
    case 'tool_call':
      return str(p.title) ?? 'Used a tool';
    case 'tool_result':
      return `${str(p.title) ?? 'Tool'} — ${str(p.status) ?? 'done'}`;
    case 'plan':
      return 'Updated its checklist';
    case 'permission':
      return `${p.decision === 'allowed' ? 'Allowed' : 'Refused'}: ${str(p.title) ?? str(p.kind) ?? 'a tool'}`;
    case 'test': {
      const exit = num(p.exit) ?? -1;
      return exit === 0 ? 'Tests passed' : `Tests failed (exit ${exit})`;
    }
    case 'status':
      return str(p.note) ?? `Status: ${statusLabel(str(p.status) ?? '')}`;
    case 'proxy':
      switch (p.action) {
        case 'call': {
          const ran = Math.round(num(p.ran) ?? 0);
          const waited = num(p.waited) ?? 0;
          return waited >= 1
            ? `Model: ${ran}s (waited ${Math.round(waited)}s for the GPU)`
            : `Model: ${ran}s`;
        }
        case 'preempted':
          return 'Paused for a chat message';
        case 'paused':
          return 'Waiting: GPU inference is paused';
        case 'paused_out':
          return 'Gave up waiting for the GPU';
        case 'promoted':
          return 'Rescued a tool call from its thinking';
        case 'poked':
          return 'Nudged it to make a tool call properly';
        case 'stuck':
          return 'Stuck: tool calls keep landing in its thinking';
        default:
          return 'Model';
      }
    default:
      return e.kind;
  }
}

export type DiffLineKind = 'context' | 'added' | 'removed' | 'note';
export interface DiffLine {
  kind: DiffLineKind;
  text: string;
  oldNumber: number | null;
  newNumber: number | null;
}
export interface DiffHunk {
  header: string;
  lines: DiffLine[];
}
export interface DiffFile {
  path: string;
  oldPath: string | null;
  isNew: boolean;
  isDeleted: boolean;
  isBinary: boolean;
  hunks: DiffHunk[];
}

function hunkStarts(header: string): [number, number] {
  const start = (marker: string) => {
    const token = header.split(' ').find(t => t.startsWith(marker));
    return token ? parseInt(token.slice(1).split(',')[0], 10) || 1 : 1;
  };
  return [start('-'), start('+')];
}

/** `git diff` text -> files, hunks and numbered lines. Mirrors UnifiedDiff.parse. */
export function parseDiff(patch: string): DiffFile[] {
  const files: DiffFile[] = [];
  let file: DiffFile | null = null;
  let hunk: DiffHunk | null = null;
  let oldLine = 0;
  let newLine = 0;

  const closeFile = () => {
    if (file) {
      if (file.oldPath === file.path) file.oldPath = null;
      files.push(file);
    }
    file = null;
    hunk = null;
  };

  for (const line of patch.split('\n')) {
    if (line.startsWith('diff --git ')) {
      closeFile();
      const rest = line.slice('diff --git '.length);
      const at = rest.lastIndexOf(' b/');
      const a = at >= 0 ? rest.slice(0, at) : rest;
      file = {
        path: at >= 0 ? rest.slice(at + 3) : rest,
        oldPath: a.startsWith('a/') ? a.slice(2) : a,
        isNew: false,
        isDeleted: false,
        isBinary: false,
        hunks: [],
      };
      continue;
    }
    if (!file) continue;
    const f: DiffFile = file;
    if (!hunk) {
      if (line.startsWith('new file mode')) {
        f.isNew = true;
        continue;
      }
      if (line.startsWith('deleted file mode')) {
        f.isDeleted = true;
        continue;
      }
      if (line.startsWith('Binary files')) {
        f.isBinary = true;
        continue;
      }
      if (line.startsWith('rename from ')) {
        f.oldPath = line.slice('rename from '.length);
        continue;
      }
      if (line.startsWith('rename to ')) {
        f.path = line.slice('rename to '.length);
        continue;
      }
      if (/^(--- |\+\+\+ |index |similarity |old mode|new mode)/.test(line))
        continue;
    }
    if (line.startsWith('@@')) {
      hunk = { header: line, lines: [] };
      f.hunks.push(hunk);
      [oldLine, newLine] = hunkStarts(line);
      continue;
    }
    const h: DiffHunk | null = hunk;
    if (!h) continue;
    if (line.startsWith('+')) {
      h.lines.push({
        kind: 'added',
        text: line.slice(1),
        oldNumber: null,
        newNumber: newLine++,
      });
    } else if (line.startsWith('-')) {
      h.lines.push({
        kind: 'removed',
        text: line.slice(1),
        oldNumber: oldLine++,
        newNumber: null,
      });
    } else if (line.startsWith('\\')) {
      h.lines.push({
        kind: 'note',
        text: line,
        oldNumber: null,
        newNumber: null,
      });
    } else if (line.startsWith(' ')) {
      h.lines.push({
        kind: 'context',
        text: line.slice(1),
        oldNumber: oldLine++,
        newNumber: newLine++,
      });
    }
  }
  closeFile();
  return files;
}
