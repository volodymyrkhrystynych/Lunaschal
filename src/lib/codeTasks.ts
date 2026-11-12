import type { CodeTask, CodeTaskEvent } from '../hooks/api';

/**
 * The Code tab's rules, shared word for word with the iPhone's
 * `LunaschalCore/CodeAgent.swift` — `CodeAgentTests.testStatusLabelsMatchTheWeb`
 * and this file's test pin the same strings, so the two clients describe a
 * task the same way.
 */

export const ACTIVE_STATES = new Set([
  'queued',
  'answering',
  'planning',
  'running',
  'testing',
]);
export const CLOSED_STATES = new Set(['cancelled', 'pushed', 'discarded']);
const NEEDS_YOU = new Set([
  'answered',
  'awaiting_plan',
  'needs_input',
  'done',
  'error',
  'interrupted',
]);

const LABELS: Record<string, string> = {
  queued: 'Queued',
  answering: 'Looking into it',
  answered: 'Answered',
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
  [
    'answered',
    'needs_input',
    'done',
    'error',
    'interrupted',
    'awaiting_plan',
  ].includes(t.status);
/** An answered question can become a change, planned in the same session. */
export const canPlanFromAnswer = (t: CodeTask) => t.status === 'answered';

/** What a new task is: a question to answer, or a change to plan and build. */
export type CodeTaskMode = 'ask' | 'plan';
export const MODES: Record<
  CodeTaskMode,
  { title: string; placeholder: string; start: string; explanation: string }
> = {
  ask: {
    title: 'Ask',
    placeholder: 'Ask a question about the code…',
    start: 'Ask',
    explanation:
      'The agent reads the code and answers. It changes nothing, and you can turn the answer into a plan.',
  },
  plan: {
    title: 'Change',
    placeholder: 'Describe a change for the agent…',
    start: 'Start planning',
    explanation:
      'The agent explores the code and writes a plan first. Nothing changes until you approve it.',
  },
};
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
          let label = `Model: ${ran}s`;
          // What llama-server prefilled versus reused: a long call with a big
          // "new" number is a lost prompt cache, not a slow model.
          const prefilled = num(p.prefilled);
          if (prefilled !== undefined) {
            const cached = num(p.cached) ?? 0;
            label += ` · ${tokens(prefilled)} new`;
            if (cached > 0) label += `, ${tokens(cached)} cached`;
          }
          return waited >= 1
            ? `${label} (waited ${Math.round(waited)}s for the GPU)`
            : label;
        }
        case 'lookup':
          return lookupLabel(p);
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

/** 812, 4.3k, 150k. */
export function tokens(n: number): string {
  if (n < 1000) return String(Math.trunc(n));
  if (n < 10000) return `${(n / 1000).toFixed(1)}k`;
  return `${Math.round(n / 1000)}k`;
}

/** A library, wiki, code-map or web lookup the agent made through the host. */
function lookupLabel(p: Record<string, unknown>): string {
  const rawArg = str(p.arg);
  const arg = rawArg !== undefined ? `\u2018${rawArg}\u2019` : '';
  const ok = p.ok === true;
  const count = num(p.count);
  const found = (noun: string) =>
    count !== undefined
      ? `${Math.trunc(count)} ${noun}${count === 1 ? '' : 's'}`
      : `${noun}s`;
  switch (p.tool) {
    case 'library_search':
      return ok
        ? `Library: ${found('result')} for ${arg}`
        : `Library search failed: ${arg}`;
    case 'library_read':
      return ok
        ? `Read from the library: ${rawArg ?? 'an article'}`
        : "Couldn't read from the library";
    case 'web_research': {
      if (p.blocked === true)
        return 'Web research refused: search the library first';
      const secs = Math.round(num(p.seconds) ?? 0);
      return ok
        ? `Web research: ${found('source')} for ${arg} in ${secs}s`
        : `Web research failed: ${arg}`;
    }
    case 'repo_wiki_search':
      return `Wiki: ${found('note')} for ${arg}`;
    case 'repo_wiki_read':
      return ok ? `Read wiki note ${rawArg ?? ''}` : 'No such wiki note';
    case 'code_map':
      return `Code map: ${arg}`;
    default:
      return 'Looked something up';
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

export type TerminalStyle =
  | 'prompt'
  | 'text'
  | 'tool'
  | 'output'
  | 'thought'
  | 'dim'
  | 'success'
  | 'failure';

export interface TerminalLine {
  id: string;
  style: TerminalStyle;
  text: string;
}

const lowerFirst = (s: string) => s.slice(0, 1).toLowerCase() + s.slice(1);

/**
 * The event log laid out as a terminal session. Same lines as
 * apple/LunaschalCore's CodeTerminal, so phone and desktop show one
 * transcript: plumbing included as dim lines, noise (a pending tool, an
 * allowed write) left out.
 */
export function terminalLines(events: CodeTaskEvent[]): TerminalLine[] {
  const out: TerminalLine[] = [];
  for (const e of events) {
    const id = `e${e.seq}`;
    const add = (style: TerminalStyle, text: string, suffix = '') =>
      out.push({ id: id + suffix, style, text });
    const p = e.payload;
    switch (e.kind) {
      case 'user':
        add('prompt', '❯ ' + (str(p.text) ?? ''));
        break;
      case 'message':
      case 'thought': {
        const text = (str(p.text) ?? '').trim();
        if (text) add(e.kind === 'message' ? 'text' : 'thought', text);
        break;
      }
      case 'tool_call':
        add('tool', '● ' + (str(p.title) ?? 'tool'));
        break;
      case 'tool_result':
        if (p.status === 'failed') add('failure', '  ⎿ failed');
        else if (p.status === 'completed') add('dim', '  ⎿ done');
        break;
      case 'permission':
        if (p.decision !== 'allowed')
          add(
            'dim',
            `  ⎿ refused: ${str(p.title) ?? str(p.kind) ?? 'a tool'} (the plan step is read-only)`
          );
        break;
      case 'plan': {
        const entries = Array.isArray(p.entries) ? p.entries : [];
        if (!entries.length) break;
        add(
          'dim',
          entries
            .map(raw => {
              const entry = (raw ?? {}) as Record<string, unknown>;
              const mark =
                entry.status === 'completed'
                  ? '☒'
                  : entry.status === 'in_progress'
                    ? '◐'
                    : '☐';
              return `  ${mark} ${str(entry.content) ?? ''}`;
            })
            .join('\n')
        );
        break;
      }
      case 'proxy': {
        const failed = p.action === 'stuck' || p.action === 'paused_out';
        add(failed ? 'failure' : 'dim', '  · ' + lowerFirst(eventLabel(e)));
        break;
      }
      case 'test': {
        const exit = num(p.exit) ?? -1;
        add(
          exit === 0 ? 'success' : 'failure',
          exit === 0 ? '✓ tests passed' : `✗ tests failed (exit ${exit})`
        );
        const tail = (str(p.tail) ?? '').trim();
        if (tail) add('output', tail, '-tail');
        break;
      }
      case 'status': {
        const note = str(p.note);
        const error = str(p.error);
        const status = str(p.status);
        if (note) add('dim', '  ! ' + note);
        else if (error) add('failure', '✗ ' + error);
        else if (status)
          add('dim', `── ${statusLabel(status).toLowerCase()} ──`);
        break;
      }
      default:
        add('dim', eventLabel(e));
    }
  }
  return out;
}

/** Dictation adds to what is typed, so a second clip carries on the first. */
export function appendDictation(draft: string, words: string): string {
  const w = words.trim();
  if (!w) return draft;
  if (!draft) return w;
  return draft + (/\s$/.test(draft) ? '' : ' ') + w;
}

/**
 * The test verdict worth showing: none while the agent is at work, because
 * the stored one describes code it is in the middle of changing — a red
 * "tests failed" under a running follow-up read as the follow-up's result.
 */
export function testVerdict(t: CodeTask): 'passed' | 'failed' | null {
  if (isActive(t) || t.testExit === null || t.testExit === undefined)
    return null;
  return t.testExit === 0 ? 'passed' : 'failed';
}

const HEADLINES: Record<string, string> = {
  queued: 'Queued — starts when the agent is free',
  answering: 'The agent is looking into it',
  planning: 'The agent is planning',
  running: 'The agent is working',
  testing: 'Running the test suite',
};

export interface CodeActivity {
  headline: string;
  /** When the current status began (ms since the epoch), if known. */
  since: number | null;
  /** The latest thing it did in this status, if anything. */
  doing: string | null;
}

/**
 * What a running task is doing right now, for the banner that says so. Same
 * wording as `CodeActivity` in LunaschalCore/CodeAgent.swift.
 */
export function activity(
  t: CodeTask,
  events: CodeTaskEvent[]
): CodeActivity | null {
  if (!isActive(t)) return null;
  let start = -1;
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.kind === 'status' && e.payload.status === t.status) {
      start = i;
      break;
    }
  }
  const sinceEvent = start >= 0 ? events[start] : null;
  const since = sinceEvent?.createdAt ? Date.parse(sinceEvent.createdAt) : NaN;
  let doing: string | null = null;
  if (t.status !== 'testing') {
    for (let i = events.length - 1; i > start; i--) {
      doing = stepLabel(events[i]);
      if (doing) break;
    }
  }
  return {
    headline: HEADLINES[t.status] ?? statusLabel(t.status),
    since: Number.isNaN(since) ? null : since,
    doing,
  };
}

function stepLabel(e: CodeTaskEvent): string | null {
  switch (e.kind) {
    case 'tool_call': {
      const title = (str(e.payload.title) ?? 'a tool').split('\n')[0].trim();
      return 'Running: ' + clip(title, 90);
    }
    case 'thought':
      return 'Thinking it over';
    case 'message':
      return 'Writing its reply';
    case 'proxy':
      return clip(eventLabel(e), 90);
    default:
      return null;
  }
}

/** Cut at `n` Unicode scalars — what Swift's `unicodeScalars` counts too. */
export function clip(s: string, n: number): string {
  const chars = Array.from(s);
  return chars.length <= n ? s : chars.slice(0, n - 1).join('') + '…';
}

/**
 * The events already held plus a page fetched after the last of them. The
 * server returns at most 500 per request, oldest first, so a long task's log
 * is read in pages and kept — reading it whole on every poll showed only its
 * first 500 events, and `activity` then found the first turn's status.
 */
export function appendEvents(
  held: CodeTaskEvent[],
  page: CodeTaskEvent[]
): CodeTaskEvent[] {
  const last = held.length ? held[held.length - 1].seq : 0;
  const fresh = page.filter(e => e.seq > last);
  return fresh.length ? [...held, ...fresh] : held;
}

/** The page size `GET /api/code/tasks/<id>` returns at most. */
export const EVENT_PAGE = 500;

/** 45s, 3m 05s, 1h 02m. */
export function elapsedLabel(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}
