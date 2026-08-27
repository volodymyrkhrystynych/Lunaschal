import { describe, it, expect } from 'vitest';
import {
  PRIORITY_META,
  entriesToText,
  filterEntries,
  formatLogTimestamp,
  isAccessLog,
  priorityMeta,
  type ServerLogEntry,
} from './serverLogs';

const entry = (over: Partial<ServerLogEntry>): ServerLogEntry => ({
  ts: '2026-08-26T22:00:00-04:00',
  priority: 6,
  identifier: 'run-prod.sh',
  message: 'something happened',
  ...over,
});

describe('filterEntries', () => {
  const entries = [
    entry({ message: 'newspapers sync failed: ConnectionError', priority: 3 }),
    entry({ message: 'briefing generated ok', priority: 6 }),
    entry({
      message:
        '100.95.99.65 - - [26/Aug/2026 22:00:00] "GET /api/health HTTP/1.1" 200 -',
      priority: 6,
    }),
  ];

  it('matches the query case-insensitively against the message', () => {
    const out = filterEntries(entries, { query: 'CONNECTION' });
    expect(out).toHaveLength(1);
    expect(out[0].message).toContain('ConnectionError');
  });

  it('keeps only entries at or above the severity cutoff', () => {
    const out = filterEntries(entries, { maxPriority: 3 });
    expect(out.map(e => e.priority)).toEqual([3]);
  });

  it('drops access-log lines but keeps app lines when hideAccessLogs is set', () => {
    const out = filterEntries(entries, { hideAccessLogs: true });
    expect(out).toHaveLength(2);
    expect(out.some(e => e.message.includes('HTTP/1.1'))).toBe(false);
  });

  it('applies all filters together', () => {
    const out = filterEntries(entries, {
      query: 'ok',
      maxPriority: 6,
      hideAccessLogs: true,
    });
    expect(out.map(e => e.message)).toEqual(['briefing generated ok']);
  });

  it('is a no-op with default options', () => {
    expect(filterEntries(entries, {})).toHaveLength(3);
  });
});

describe('isAccessLog', () => {
  it('recognises a werkzeug request line', () => {
    expect(
      isAccessLog('1.2.3.4 - - [x] "POST /api/chat/stream HTTP/1.1" 500 -')
    ).toBe(true);
  });
  it('does not flag an ordinary log line that mentions a path', () => {
    expect(isAccessLog('wrote /api/journal cache to disk')).toBe(false);
  });
});

describe('formatLogTimestamp', () => {
  // Constructed without an offset so they are the same wall-clock instant in
  // whatever timezone the test host runs in (same trick as sleep.test.ts).
  const now = new Date('2026-08-26T22:30:00');

  it('shows only the time for an entry from today', () => {
    expect(formatLogTimestamp('2026-08-26T09:05:07', now)).toBe('09:05:07');
  });

  it('includes the date for an entry from another day', () => {
    expect(formatLogTimestamp('2026-08-24T09:05:07', now)).toMatch(
      /Aug 24 09:05:07/
    );
  });

  it('degrades for a missing or unparseable timestamp', () => {
    expect(formatLogTimestamp(null, now)).toBe('--:--:--');
    expect(formatLogTimestamp('not-a-date', now)).toBe('--:--:--');
  });
});

describe('priority metadata', () => {
  it('has an entry for every journald priority 0-7', () => {
    for (let p = 0; p <= 7; p++) {
      expect(PRIORITY_META[p]).toBeDefined();
      expect(PRIORITY_META[p].label).toBeTruthy();
    }
  });

  it('falls back to info for an out-of-range priority', () => {
    expect(priorityMeta(99)).toBe(PRIORITY_META[6]);
  });
});

describe('entriesToText', () => {
  it('renders one line per entry with timestamp and level', () => {
    const text = entriesToText([
      entry({ message: 'a', priority: 3 }),
      entry({ message: 'b', priority: 6 }),
    ]);
    expect(text.split('\n')).toHaveLength(2);
    expect(text).toContain('error\ta');
    expect(text).toContain('info\tb');
  });
});
