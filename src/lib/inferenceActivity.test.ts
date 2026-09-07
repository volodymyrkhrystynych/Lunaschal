import { describe, expect, it } from 'vitest';
import {
  activityToText,
  countersSentence,
  eventBadge,
  eventLine,
  formatEventTime,
  formatSeconds,
  jobLine,
  orphanedKinds,
  type InferenceActivity,
  type InferenceEvent,
  type InferenceJob,
} from './inferenceActivity';

const at = 1_757_000_000;

function event(e: Partial<InferenceEvent>): InferenceEvent {
  return { at, kind: 'call', ...e } as InferenceEvent;
}

function job(j: Partial<InferenceJob>): InferenceJob {
  return {
    id: 'j1',
    kind: 'journal.polish',
    targetId: null,
    status: 'pending',
    attempts: 0,
    cancels: 0,
    error: null,
    createdAt: null,
    startedAt: null,
    finishedAt: null,
    ...j,
  };
}

describe('formatSeconds', () => {
  it('keeps a decimal where the difference is worth seeing', () => {
    expect(formatSeconds(2.34)).toBe('2.3s');
  });

  it('drops it once the number is big enough not to need it', () => {
    expect(formatSeconds(42.6)).toBe('43s');
  });

  it('switches to minutes for a long generation', () => {
    expect(formatSeconds(185)).toBe('3m 5s');
    expect(formatSeconds(180)).toBe('3m');
  });

  it('renders nothing rather than NaN for a missing duration', () => {
    expect(formatSeconds(undefined)).toBe('');
  });
});

describe('formatEventTime', () => {
  it('is a wall clock, matching the journal viewer below it', () => {
    expect(formatEventTime(at)).toMatch(/^\d\d:\d\d:\d\d$/);
  });

  it('degrades rather than printing Invalid Date', () => {
    expect(formatEventTime(NaN)).toBe('--:--:--');
  });
});

describe('eventLine', () => {
  it('reports the wait separately from the run', () => {
    const line = eventLine(
      event({ label: 'chat.reply', ran: 3.2, waited: 1.4, outcome: 'ok' })
    );
    expect(line.text).toContain('3.2s');
    expect(line.text).toContain('1.4s queued');
    expect(line.tone).toBe('muted');
  });

  it('leaves the wait out when there effectively was none', () => {
    const line = eventLine(
      event({ label: 'chat.reply', ran: 3.2, waited: 0.01, outcome: 'ok' })
    );
    expect(line.text).not.toContain('queued');
  });

  it('carries the exception through on a failure', () => {
    const line = eventLine(
      event({
        label: 'chat.reply',
        ran: 0.4,
        outcome: 'error',
        detail: 'APIConnectionError: Connection refused',
      })
    );
    expect(line.tone).toBe('error');
    expect(line.text).toContain('Connection refused');
  });

  it('names what took the lane on a preemption', () => {
    const line = eventLine(
      event({
        kind: 'preempt',
        label: 'journal.polish',
        by: 'chat.reply',
        ran: 12,
      })
    );
    expect(line.text).toBe('journal.polish cancelled for chat.reply after 12s');
    expect(line.tone).toBe('warn');
  });

  it('says why a call never started', () => {
    const line = eventLine(
      event({ kind: 'refused', label: 'chat.reply', lane: 'gpu', priority: 1 })
    );
    expect(line.text).toContain('GPU inference is paused');
    expect(line.badge).toBe('GPU P1');
  });

  it('shows the pause itself, so the refusals under it have a cause', () => {
    expect(
      eventLine(event({ kind: 'pause', detail: 'qwen36 unloaded' })).text
    ).toBe('Paused — qwen36 unloaded');
    expect(
      eventLine(event({ kind: 'resume', detail: '2 job(s) queued' })).text
    ).toBe('Resumed — 2 job(s) queued');
  });

  it('flags a leaked slot as the bug it is', () => {
    const line = eventLine(
      event({ kind: 'leak', label: 'chat.reply', age: 1800 })
    );
    expect(line.tone).toBe('error');
    expect(line.text).toContain('leaked');
  });
});

describe('eventBadge', () => {
  it('omits what it does not know rather than printing a blank', () => {
    expect(eventBadge(event({ kind: 'pause' }))).toBe('');
    expect(eventBadge(event({ lane: 'cpu' }))).toBe('CPU');
  });
});

describe('jobLine', () => {
  it('is just the kind and target when nothing has gone wrong', () => {
    expect(jobLine(job({ targetId: 'entry-1' }))).toBe(
      'journal.polish for entry-1'
    );
  });

  it('spells out preemptions rather than showing a bare count', () => {
    // A count on its own reads as damage; a job preempted twice is the design
    // working.
    expect(jobLine(job({ cancels: 1, attempts: 2 }))).toContain(
      'preempted once'
    );
    expect(jobLine(job({ cancels: 2, attempts: 3 }))).toContain('preempted 2×');
  });

  it('surfaces the error, which is the only reason to read this list', () => {
    expect(
      jobLine(job({ status: 'error', attempts: 1, error: 'model refused' }))
    ).toContain('model refused');
  });
});

describe('countersSentence', () => {
  it('says so plainly when nothing has run', () => {
    expect(countersSentence(undefined)).toBe('No model calls yet this run.');
  });

  it('mentions only the counts that are not zero', () => {
    const s = countersSentence({
      calls: 12,
      preempted: 2,
      refused: 0,
      errors: 1,
      leaked: 0,
    });
    expect(s).toContain('12 calls');
    expect(s).toContain('2 preempted');
    expect(s).toContain('1 failed');
    expect(s).not.toContain('refused');
    expect(s).not.toContain('leaked');
  });
});

describe('orphanedKinds', () => {
  it('finds a job whose handler was renamed away', () => {
    expect(
      orphanedKinds(
        [job({ kind: 'journal.polish' }), job({ id: 'j2', kind: 'gone.away' })],
        ['journal.polish']
      )
    ).toEqual(['gone.away']);
  });

  it('ignores jobs that already finished — those needed no handler again', () => {
    expect(
      orphanedKinds([job({ kind: 'gone.away', status: 'done' })], [])
    ).toEqual([]);
  });
});

describe('activityToText', () => {
  it('copies both halves, not whichever list was scrolled into view', () => {
    const activity: InferenceActivity = {
      events: [event({ label: 'chat.reply', ran: 1, outcome: 'ok' })],
      counters: { calls: 1, preempted: 0, refused: 0, errors: 0, leaked: 0 },
      lanes: {},
      jobs: [job({ status: 'error', error: 'boom' })],
      jobCounts: { error: 1 },
      handlers: ['journal.polish'],
    };
    const text = activityToText(activity);
    expect(text).toContain('EVENTS');
    expect(text).toContain('chat.reply');
    expect(text).toContain('JOBS');
    expect(text).toContain('boom');
  });

  it('is empty rather than throwing before the first fetch', () => {
    expect(activityToText(undefined)).toBe('');
  });
});

describe('an abandoned stream', () => {
  it('reads as ordinary, because a closed chat tab is not a failure', () => {
    const line = eventLine(
      event({ label: 'chat_stream', ran: 4, outcome: 'abandoned' })
    );
    expect(line.tone).toBe('muted');
    expect(line.text).toContain('abandoned by its caller');
  });
});
