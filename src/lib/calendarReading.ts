import { minutesFromDayStart, type SleepDay } from './sleep';

export interface CalendarReadingSpan {
  id: string;
  title: string;
  chapterNumber: number;
  startedAt: number;
  endedAt: number;
}

/** Unknown boundaries use the day edges; known wake/sleep times trim the rail. */
export function readingLayout(
  date: string,
  sleep: SleepDay,
  spans: CalendarReadingSpan[]
) {
  const clamp = (value: number) => Math.max(0, Math.min(1440, value));
  const start = clamp(
    sleep.wakeAt === null ? 0 : minutesFromDayStart(sleep.wakeAt, date)
  );
  const end = clamp(
    sleep.sleepAt === null ? 1440 : minutesFromDayStart(sleep.sleepAt, date)
  );
  return {
    start,
    end,
    spans: spans
      .map(span => ({
        ...span,
        start: Math.max(start, minutesFromDayStart(span.startedAt, date)),
        end: Math.min(end, minutesFromDayStart(span.endedAt, date)),
      }))
      .filter(span => span.end > span.start),
  };
}
