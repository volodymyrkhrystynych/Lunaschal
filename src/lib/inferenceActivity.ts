/**
 * Reading the LLM service's activity log — Settings → Logs.
 *
 * Pure and node-testable, same reason as src/lib/serverLogs.ts: the backend
 * (backend/ai/service.py) decides what an event *is*, and this decides how it
 * reads. Keeping the wording here means it can be pinned by a test without a
 * DOM, and means the two panels that show this state cannot describe it
 * differently.
 */

export type InferenceEventKind =
  'call' | 'preempt' | 'refused' | 'leak' | 'pause' | 'resume';

export interface InferenceEvent {
  /** Unix seconds, float. Wall clock — durations come as their own fields. */
  at: number;
  kind: InferenceEventKind;
  lane?: string;
  label?: string;
  priority?: number;
  /** Seconds spent queued before the call was admitted. */
  waited?: number;
  /** Seconds the call ran for. */
  ran?: number;
  outcome?: 'ok' | 'preempted' | 'paused' | 'abandoned' | 'error';
  /** The interactive call that took the lane, on a `preempt`. */
  by?: string;
  /** Seconds a leaked slot had been held. */
  age?: number;
  detail?: string | null;
}

/** One row of `llm_jobs`, as row_to_dict renders it (ISO timestamps). */
export interface InferenceJob {
  id: string;
  kind: string;
  targetId: string | null;
  status: 'pending' | 'running' | 'done' | 'error';
  attempts: number;
  cancels: number;
  error: string | null;
  createdAt: string | null;
  startedAt: string | null;
  finishedAt: string | null;
}

export interface InferenceCounters {
  calls: number;
  preempted: number;
  refused: number;
  errors: number;
  leaked: number;
}

export interface InferenceActivity {
  events: InferenceEvent[];
  counters: InferenceCounters;
  lanes: Record<string, unknown>;
  jobs: InferenceJob[];
  jobCounts: Partial<Record<InferenceJob['status'], number>>;
  handlers: string[];
}

export type ActivityTone = 'ok' | 'muted' | 'warn' | 'error';

export const ACTIVITY_TONE: Record<ActivityTone, string> = {
  ok: 'text-[var(--color-text)]',
  muted: 'text-[var(--color-text-muted)]',
  warn: 'text-amber-400',
  error: 'text-red-400',
};

function pad(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

/** `HH:MM:SS` from unix seconds. Same shape as serverLogs' journal stamps, so
 *  the two logs can be read one after the other without re-adjusting. */
export function formatEventTime(at: number): string {
  if (!Number.isFinite(at)) return '--:--:--';
  const d = new Date(at * 1000);
  if (Number.isNaN(d.getTime())) return '--:--:--';
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** Seconds, at the precision that is actually informative at that scale. */
export function formatSeconds(s: number | undefined): string {
  if (s === undefined || s === null || !Number.isFinite(s)) return '';
  if (s < 10) return `${s.toFixed(1)}s`;
  if (s < 90) return `${Math.round(s)}s`;
  const mins = Math.floor(s / 60);
  const rest = Math.round(s % 60);
  return rest ? `${mins}m ${rest}s` : `${mins}m`;
}

/** `GPU P1` — which queue the call was in and whether anyone was waiting on it. */
export function eventBadge(e: InferenceEvent): string {
  const lane = e.lane ? e.lane.toUpperCase() : '';
  const priority = e.priority ? `P${e.priority}` : '';
  return [lane, priority].filter(Boolean).join(' ');
}

export interface EventLine {
  time: string;
  badge: string;
  tone: ActivityTone;
  text: string;
}

export function eventLine(e: InferenceEvent): EventLine {
  const time = formatEventTime(e.at);
  const badge = eventBadge(e);
  const label = e.label ?? 'a call';

  switch (e.kind) {
    case 'call': {
      // The wait is reported separately from the run because they have
      // completely different causes — a slow model versus a busy lane — and a
      // single elapsed number hides which one you are looking at.
      const waited =
        e.waited && e.waited >= 0.1
          ? `, ${formatSeconds(e.waited)} queued`
          : '';
      if (e.outcome === 'error') {
        return {
          time,
          badge,
          tone: 'error',
          text: `${label} failed after ${formatSeconds(e.ran)}${waited} — ${e.detail ?? 'no detail'}`,
        };
      }
      if (e.outcome === 'preempted') {
        return {
          time,
          badge,
          tone: 'warn',
          text: `${label} gave up the lane after ${formatSeconds(e.ran)}${waited}`,
        };
      }
      if (e.outcome === 'abandoned') {
        // The caller went away mid-stream — a chat tab closed. Muted on
        // purpose: nothing is wrong, and colouring it would train the eye to
        // ignore the lines that are.
        return {
          time,
          badge,
          tone: 'muted',
          text: `${label} abandoned by its caller after ${formatSeconds(e.ran)}`,
        };
      }
      if (e.outcome === 'paused') {
        return {
          time,
          badge,
          tone: 'warn',
          text: `${label} stopped after ${formatSeconds(e.ran)} — inference was paused mid-call`,
        };
      }
      return {
        time,
        badge,
        tone: 'muted',
        text: `${label} finished in ${formatSeconds(e.ran)}${waited}`,
      };
    }
    case 'preempt':
      return {
        time,
        badge,
        tone: 'warn',
        text: `${label} cancelled for ${e.by ?? 'an interactive call'} after ${formatSeconds(e.ran)}`,
      };
    case 'refused':
      return {
        time,
        badge,
        tone: 'warn',
        text: `${label} refused — GPU inference is paused`,
      };
    case 'leak':
      return {
        time,
        badge,
        tone: 'error',
        text: `${label} held a slot for ${formatSeconds(e.age)} and was dropped as leaked`,
      };
    case 'pause':
      return {
        time,
        badge,
        tone: 'warn',
        text: `Paused${e.detail ? ` — ${e.detail}` : ''}`,
      };
    case 'resume':
      return {
        time,
        badge,
        tone: 'ok',
        text: `Resumed${e.detail ? ` — ${e.detail}` : ''}`,
      };
    default:
      return { time, badge, tone: 'muted', text: `${e.kind} ${label}` };
  }
}

export const JOB_TONE: Record<InferenceJob['status'], ActivityTone> = {
  pending: 'muted',
  running: 'ok',
  done: 'muted',
  error: 'error',
};

/** What a job row says about itself, in one line.
 *
 *  `cancels` is spelled out rather than shown as a bare number: a job that has
 *  been preempted twice is working as designed, and a reader who does not know
 *  that reads any nonzero count as damage. */
export function jobLine(job: InferenceJob): string {
  const target = job.targetId ? ` for ${job.targetId}` : '';
  const bits: string[] = [];
  if (job.attempts > 1) bits.push(`${job.attempts} attempts`);
  if (job.cancels > 0)
    bits.push(
      job.cancels === 1 ? 'preempted once' : `preempted ${job.cancels}×`
    );
  if (job.status === 'error' && job.error) bits.push(job.error);
  const suffix = bits.length ? ` — ${bits.join(', ')}` : '';
  return `${job.kind}${target}${suffix}`;
}

/** Counters as a sentence, or '' when nothing has run yet. */
export function countersSentence(c: InferenceCounters | undefined): string {
  if (!c || !c.calls) return 'No model calls yet this run.';
  const parts = [`${c.calls} call${c.calls === 1 ? '' : 's'}`];
  if (c.preempted) parts.push(`${c.preempted} preempted`);
  if (c.refused) parts.push(`${c.refused} refused while paused`);
  if (c.errors) parts.push(`${c.errors} failed`);
  if (c.leaked) parts.push(`${c.leaked} leaked`);
  return `Since this server started: ${parts.join(', ')}.`;
}

/** A job whose `kind` has no registered handler will never run, and its row
 *  gives no hint of that — it just sits `pending` forever. Naming them is the
 *  cheapest way to make a renamed handler visible. */
export function orphanedKinds(
  jobs: InferenceJob[],
  handlers: string[]
): string[] {
  const known = new Set(handlers);
  const missing = new Set<string>();
  for (const j of jobs) {
    if (j.status !== 'done' && !known.has(j.kind)) missing.add(j.kind);
  }
  return [...missing].sort();
}

/** Plain text for the Copy button — both halves, so a paste carries the whole
 *  picture rather than whichever list happened to be scrolled into view. */
export function activityToText(
  activity: InferenceActivity | undefined
): string {
  if (!activity) return '';
  const lines: string[] = [countersSentence(activity.counters), '', 'EVENTS'];
  for (const e of activity.events) {
    const l = eventLine(e);
    lines.push(`${l.time} ${l.badge ? `[${l.badge}] ` : ''}${l.text}`);
  }
  lines.push('', 'JOBS');
  for (const j of activity.jobs) {
    lines.push(`${j.status.padEnd(7)} ${jobLine(j)}`);
  }
  return lines.join('\n');
}
