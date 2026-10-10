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
  const [expanded, setExpanded] = useState(false);
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
    <div className="absolute left-12 top-0 bottom-0 w-6 z-20 pointer-events-none">
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
              className="absolute left-5 border-l border-t border-emerald-400/50 w-1"
              style={{
                top: span.start * zoom,
                height: span.top - span.start * zoom + 8,
              }}
            />
            <div
              className="absolute left-6 w-48 max-w-[55vw] rounded bg-[var(--color-surface)] border border-emerald-400/40 px-2 py-1 text-xs shadow-lg pointer-events-auto"
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
            </div>
          </div>
        ))}
    </div>
  );
}
