import { CATEGORY_COLORS } from './calendarCategories';
import { minutesFromDayStart, type SleepDay } from './sleep';

export type ActivityState = 'work' | 'leisure' | 'mix';

/** One stretch of /api/calendar/activity/<date>: already folded with reading
 *  (work during reading comes back as mix) and clipped to the 4am day. */
export interface CalendarActivitySpan {
  id: string;
  state: ActivityState;
  startedAt: number;
  endedAt: number;
}

export const ACTIVITY_LABELS: Record<ActivityState, string> = {
  work: 'Work',
  leisure: 'Leisure',
  mix: 'Mix',
};

/** The calendar's own work and leisure colours, so the rail reads as the same
 *  thing an event's stripe does; mix is both, striped. */
export function activityFill(state: ActivityState): string {
  if (state !== 'mix') return CATEGORY_COLORS[state];
  const { work, leisure } = CATEGORY_COLORS;
  return `repeating-linear-gradient(180deg, ${work} 0 4px, ${leisure} 4px 8px)`;
}

/** The reading rail's bounds — waking day, else the day's edges — so the two
 *  rails start and stop together. */
export function activityLayout(
  date: string,
  sleep: SleepDay,
  spans: CalendarActivitySpan[]
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
