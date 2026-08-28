// Pure date/label helpers for the Calendar view. Dates are plain 'YYYY-MM-DD'
// strings throughout, matching the backend's wall-clock storage — no
// timestamps, no timezone conversion. `Date#toISOString()` must never be used
// to derive one of these strings: it reads the *UTC* calendar date, which is a
// day off from the local one for several hours around midnight, so events land
// on the wrong cell and "today" highlights the wrong day.

export const WEEKDAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
// Sunday=0, matching Date#getDay(), the backend's repeat_byweekday CSV, and
// the left-to-right order of the grid.
export const WEEKDAY_INITIALS = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];

export type RepeatFreq = 'daily' | 'weekly' | 'monthly' | 'yearly';

export interface MonthCell {
  date: Date;
  iso: string;
  day: number;
  inMonth: boolean;
}

// Local 'YYYY-MM-DD' for a Date. The counterpart of todayISO in ./newspapers.
export function toLocalISO(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

// Always 42 cells (6 weeks, Sunday-first), padded with the tail of the previous
// month and the head of the next. A fixed cell count is what lets the grid
// divide the available height evenly without changing shape between a 4-row
// February and a 6-row August.
export function buildMonthGrid(year: number, month: number): MonthCell[] {
  const firstOfMonth = new Date(year, month, 1);
  const gridStart = new Date(year, month, 1 - firstOfMonth.getDay());
  return Array.from({ length: 42 }, (_, i) => {
    const date = new Date(
      gridStart.getFullYear(),
      gridStart.getMonth(),
      gridStart.getDate() + i
    );
    return {
      date,
      iso: toLocalISO(date),
      day: date.getDate(),
      inMonth: date.getMonth() === month && date.getFullYear() === year,
    };
  });
}

export function weekdayCsvToArray(csv: string | null | undefined): number[] {
  if (!csv) return [];
  return csv
    .split(',')
    .map(part => Number(part.trim()))
    .filter(n => Number.isInteger(n) && n >= 0 && n <= 6)
    .sort((a, b) => a - b);
}

// "weekdays", "Mon, Wed", "every 2 weeks on Fri", "every month".
export function repeatLabel(event: {
  repeatFreq?: RepeatFreq | string | null;
  repeatInterval?: number | null;
  repeatByweekday?: string | null;
}): string {
  const freq = event.repeatFreq;
  if (!freq) return '';
  const interval = event.repeatInterval || 1;
  const unit =
    freq === 'daily'
      ? 'day'
      : freq === 'weekly'
        ? 'week'
        : freq === 'yearly'
          ? 'year'
          : 'month';
  const every = interval === 1 ? `every ${unit}` : `every ${interval} ${unit}s`;
  if (freq !== 'weekly') return every;

  const days = weekdayCsvToArray(event.repeatByweekday);
  if (days.length === 0) return every;
  const named =
    days.length === 5 && days.every(d => d >= 1 && d <= 5)
      ? 'weekdays'
      : days.length === 7
        ? 'every day'
        : days.map(d => WEEKDAY_LABELS[d]).join(', ');
  return interval === 1 ? named : `${every} on ${named}`;
}

// The `tags` column is a JSON array string. Legacy and hand-edited rows can
// hold anything, and a throw here would take down the whole render — so bad
// values degrade to "no tags".
export function parseEventTags(tags: string | null | undefined): string[] {
  if (!tags) return [];
  try {
    const parsed = JSON.parse(tags);
    return Array.isArray(parsed)
      ? parsed.filter(t => typeof t === 'string')
      : [];
  } catch {
    return [];
  }
}

// 'HH:MM' start (+ optional end) as one compact span, or '' when untimed.
export function timeSpan(
  time?: string | null,
  endTime?: string | null
): string {
  if (!time) return '';
  return endTime ? `${time}–${endTime}` : time;
}

// What the clock line reads for an event. The explicit all-day flag wins over
// any leftover time; an event that is merely untimed still shows nothing, which
// is what rows predating the flag have always done.
export function eventTimeLabel(event: {
  time?: string | null;
  endTime?: string | null;
  allDay?: boolean | number | null;
}): string {
  if (event.allDay) return 'All day';
  return timeSpan(event.time, event.endTime);
}

// The recurrence rule in the shape the two forms edit it.
//
// The create form and the edit modal both hold the rule like this and hand it
// to the same <RepeatFields>, so a control added to one is a control added to
// both. The stored row keeps its weekdays as a CSV string and every field
// nullable; a draft keeps them as an array and never null, because a cleared
// <select> is '' and not null. The two functions below are the edges of that
// conversion.
export interface RepeatDraft {
  freq: '' | RepeatFreq;
  interval: number;
  byweekday: number[];
  until: string;
}

export const EMPTY_REPEAT: RepeatDraft = {
  freq: '',
  interval: 1,
  byweekday: [],
  until: '',
};

// The noun the "Every N ___" box counts. Yearly used to fall through to the
// monthly label here, so a rule repeating every N years read "every N months".
export function repeatUnitLabel(freq: '' | RepeatFreq): string {
  switch (freq) {
    case 'daily':
      return 'day(s)';
    case 'weekly':
      return 'week(s)';
    case 'yearly':
      return 'year(s)';
    default:
      return 'month(s)';
  }
}

export function repeatDraftFromEvent(event: {
  repeatFreq?: RepeatFreq | string | null;
  repeatInterval?: number | null;
  repeatByweekday?: string | null;
  repeatUntil?: string | null;
}): RepeatDraft {
  const freq = event.repeatFreq;
  const known = (['daily', 'weekly', 'monthly', 'yearly'] as const).find(
    f => f === freq
  );
  return {
    freq: known ?? '',
    interval:
      event.repeatInterval && event.repeatInterval > 0
        ? event.repeatInterval
        : 1,
    byweekday: weekdayCsvToArray(event.repeatByweekday),
    until: event.repeatUntil ?? '',
  };
}

// The four columns as the API takes them. Explicit nulls rather than omitted
// keys: `repeatFreq: null` is what turns a series back into a one-off, and the
// parameters have to be cleared alongside it or a later re-enable inherits
// stale weekdays. Weekdays only mean anything for a weekly rule.
export function repeatDraftToPayload(draft: RepeatDraft) {
  if (!draft.freq) {
    return {
      repeatFreq: null,
      repeatInterval: null,
      repeatByweekday: null,
      repeatUntil: null,
    };
  }
  return {
    repeatFreq: draft.freq,
    repeatInterval: Math.max(1, Math.floor(draft.interval) || 1),
    repeatByweekday:
      draft.freq === 'weekly' && draft.byweekday.length
        ? draft.byweekday
        : null,
    repeatUntil: draft.until || null,
  };
}
