// Reading spans: a chapter open and being scrolled is the reader's evidence
// that the user was reading, and when. One span is one continuous stretch of
// scrolling in one chapter; the server upserts it by id
// (PUT /api/fanfic/<fic>/reading-spans/<id>) and the briefing's day
// reconstruction reads the result. All times are unix seconds.

/** No scroll for this long ends a span; the next scroll starts a new one. */
export const IDLE_GAP_SECONDS = 5 * 60;
/** The most one pause between scrolls adds to active time. Must match
 *  _SPAN_GAP_CAP in backend/routes/fanfic.py. Reading a screenful without
 *  scrolling is still reading; a window left open on a chapter is not. */
export const GAP_CAP_SECONDS = 3 * 60;
/** How often an open span is re-sent while it keeps growing. */
export const FLUSH_INTERVAL_SECONDS = 60;

export interface ReadingSpan {
  id: string;
  ficId: string;
  chapterId: string;
  startedAt: number;
  endedAt: number;
  activeSeconds: number;
  startFraction: number;
  endFraction: number;
}

export interface SpanState {
  span: ReadingSpan | null;
  /** The span has changed since it was last handed out for flushing. */
  dirty: boolean;
  lastFlushAt: number;
}

export const emptySpanState = (): SpanState => ({
  span: null,
  dirty: false,
  lastFlushAt: 0,
});

export interface ScrollInput {
  now: number;
  fraction: number;
  ficId: string;
  chapterId: string;
  newId: () => string;
}

/** A span with no active time is a single scroll — an accidental open, or
 *  the first nudge of a span that never continued — and isn't worth a write. */
const worthSending = (state: SpanState): ReadingSpan | null =>
  state.dirty && state.span && state.span.activeSeconds > 0 ? state.span : null;

/** Record one user scroll. Returns the new state, plus the span it closed
 *  when that span still had unsent changes. */
export function recordScroll(
  state: SpanState,
  input: ScrollInput
): { state: SpanState; closed: ReadingSpan | null } {
  const { now, fraction, ficId, chapterId } = input;
  const current = state.span;
  if (
    current &&
    current.chapterId === chapterId &&
    now >= current.endedAt &&
    now - current.endedAt <= IDLE_GAP_SECONDS
  ) {
    const span: ReadingSpan = {
      ...current,
      endedAt: now,
      activeSeconds:
        current.activeSeconds +
        Math.min(now - current.endedAt, GAP_CAP_SECONDS),
      endFraction: fraction,
    };
    return { state: { ...state, span, dirty: true }, closed: null };
  }
  const closed = worthSending(state);
  const span: ReadingSpan = {
    id: input.newId(),
    ficId,
    chapterId,
    startedAt: now,
    endedAt: now,
    activeSeconds: 0,
    startFraction: fraction,
    endFraction: fraction,
  };
  return { state: { span, dirty: true, lastFlushAt: now }, closed };
}

/** The span to send now, if any: when forced (chapter change, tab hidden,
 *  unmount) or once FLUSH_INTERVAL_SECONDS has passed since the last send. */
export function takeFlush(
  state: SpanState,
  now: number,
  force = false
): { state: SpanState; flush: ReadingSpan | null } {
  const span = worthSending(state);
  if (!span || (!force && now - state.lastFlushAt < FLUSH_INTERVAL_SECONDS)) {
    return { state, flush: null };
  }
  return { state: { ...state, dirty: false, lastFlushAt: now }, flush: span };
}

/** End the current span (the chapter changed or the reader closed). */
export function closeSpan(state: SpanState): {
  state: SpanState;
  flush: ReadingSpan | null;
} {
  return { state: emptySpanState(), flush: worthSending(state) };
}
