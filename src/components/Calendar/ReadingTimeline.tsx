import { useState } from 'react';
import { readingLayout, type CalendarReadingSpan } from '@/lib/calendarReading';
import { formatClock, type SleepDay } from '@/lib/sleep';

export function ReadingTimeline({
  date,
  sleep,
  spans,
  zoom,
}: {
  date: string;
  sleep: SleepDay;
  spans: CalendarReadingSpan[];
  zoom: number;
}) {
  // Titles show by default; clicking the rail (or a title) hides them for
  // when they cover an event.
  const [expanded, setExpanded] = useState(true);
  const layout = readingLayout(date, sleep, spans);
  // Short chapters can sit closer together than two lines of text. Stagger
  // labels and connect them to their real times instead of covering a title.
  let nextLabelTop = 0;
  const labels = layout.spans.map(span => {
    const top = Math.max(span.start * zoom, nextLabelTop);
    nextLabelTop = top + 48;
    return { ...span, top };
  });
  if (layout.end <= layout.start) return null;
  return (
    <div className="absolute right-0 top-0 bottom-0 w-6 z-20 pointer-events-none">
      <button
        type="button"
        aria-label="Leisure reading timeline"
        aria-expanded={expanded}
        onClick={() => setExpanded(value => !value)}
        className="absolute w-6 pointer-events-auto rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-accent)]"
        style={{
          top: layout.start * zoom,
          height: (layout.end - layout.start) * zoom,
        }}
      >
        <span className="absolute inset-y-0 left-2.5 w-1 rounded bg-white/15" />
        {layout.spans.map(span => (
          <span
            key={span.id}
            data-testid="reading-fill"
            className="absolute left-2 w-2 rounded bg-emerald-400"
            style={{
              top: (span.start - layout.start) * zoom,
              height: (span.end - span.start) * zoom,
            }}
          />
        ))}
      </button>
      {expanded &&
        labels.map(span => (
          <div key={span.id}>
            <span
              className="absolute right-5 border-r border-t border-emerald-400/50 w-1"
              style={{
                top: span.start * zoom,
                height: span.top - span.start * zoom + 8,
              }}
            />
            <button
              type="button"
              aria-label={`Hide reading titles (${span.title} · Ch. ${span.chapterNumber})`}
              onClick={() => setExpanded(false)}
              className="absolute right-6 w-48 max-w-[55vw] text-right rounded bg-[var(--color-surface)] border border-emerald-400/40 px-2 py-1 text-xs shadow-lg pointer-events-auto"
              style={{ top: span.top }}
            >
              <div
                className="truncate"
                title={`${span.title} · Ch. ${span.chapterNumber}`}
              >
                {span.title} · Ch. {span.chapterNumber}
              </div>
              <div className="text-[var(--color-text-muted)]">
                Leisure · {formatClock(span.startedAt)}–
                {formatClock(span.endedAt)}
              </div>
            </button>
          </div>
        ))}
    </div>
  );
}
