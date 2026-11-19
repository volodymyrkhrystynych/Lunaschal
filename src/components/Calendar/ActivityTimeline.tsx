import {
  ACTIVITY_LABELS,
  activityFill,
  activityLayout,
  type CalendarActivitySpan,
} from '@/lib/calendarActivity';
import { formatClock, type SleepDay } from '@/lib/sleep';

/** Work / leisure / mix as switched on the Watch or the phone, the rail on the
 *  far right; the reading rail sits beside it. Nothing to click: the state is
 *  switched where it is lived, not edited here. */
export function ActivityTimeline({
  date,
  sleep,
  spans,
  zoom,
}: {
  date: string;
  sleep: SleepDay;
  spans: CalendarActivitySpan[];
  zoom: number;
}) {
  const layout = activityLayout(date, sleep, spans);
  if (layout.end <= layout.start || !layout.spans.length) return null;
  return (
    <div
      role="img"
      aria-label="Work and leisure timeline"
      className="absolute right-0 w-6 z-20 pointer-events-none"
      style={{
        top: layout.start * zoom,
        height: (layout.end - layout.start) * zoom,
      }}
    >
      <span className="absolute inset-y-0 left-2.5 w-1 rounded bg-white/15" />
      {layout.spans.map(span => (
        <span
          key={span.id}
          data-testid="activity-fill"
          data-state={span.state}
          title={`${ACTIVITY_LABELS[span.state]} · ${formatClock(span.startedAt)}–${formatClock(span.endedAt)}`}
          className="absolute left-2 w-2 rounded"
          style={{
            top: (span.start - layout.start) * zoom,
            height: (span.end - span.start) * zoom,
            background: activityFill(span.state),
          }}
        />
      ))}
    </div>
  );
}
