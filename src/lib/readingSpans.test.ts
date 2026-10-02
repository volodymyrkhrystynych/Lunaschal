import { describe, expect, it } from 'vitest';
import {
  FLUSH_INTERVAL_SECONDS,
  GAP_CAP_SECONDS,
  IDLE_GAP_SECONDS,
  closeSpan,
  emptySpanState,
  recordScroll,
  takeFlush,
  type SpanState,
} from './readingSpans';

let counter = 0;
const scroll = (
  state: SpanState,
  now: number,
  fraction = 0.5,
  chapterId = 'ch1'
) =>
  recordScroll(state, {
    now,
    fraction,
    ficId: 'fic',
    chapterId,
    newId: () => `span${++counter}`,
  });

describe('readingSpans', () => {
  it('accumulates active time between scrolls', () => {
    let { state } = scroll(emptySpanState(), 1000, 0.1);
    ({ state } = scroll(state, 1030, 0.2));
    ({ state } = scroll(state, 1100, 0.3));
    expect(state.span).toMatchObject({
      startedAt: 1000,
      endedAt: 1100,
      activeSeconds: 100,
      startFraction: 0.1,
      endFraction: 0.3,
    });
  });

  it('caps the credit for a long pause between scrolls', () => {
    let { state } = scroll(emptySpanState(), 1000);
    ({ state } = scroll(state, 1000 + IDLE_GAP_SECONDS));
    expect(state.span!.activeSeconds).toBe(GAP_CAP_SECONDS);
  });

  it('starts a new span after the idle gap and hands back the old one', () => {
    let { state } = scroll(emptySpanState(), 1000);
    ({ state } = scroll(state, 1060));
    const first = state.span!.id;
    const next = scroll(state, 1060 + IDLE_GAP_SECONDS + 1);
    expect(next.closed).toMatchObject({ id: first, activeSeconds: 60 });
    expect(next.state.span).toMatchObject({
      startedAt: 1060 + IDLE_GAP_SECONDS + 1,
      activeSeconds: 0,
    });
    expect(next.state.span!.id).not.toBe(first);
  });

  it('starts a new span when the chapter changes', () => {
    let { state } = scroll(emptySpanState(), 1000);
    ({ state } = scroll(state, 1060));
    const next = scroll(state, 1070, 0, 'ch2');
    expect(next.closed!.chapterId).toBe('ch1');
    expect(next.state.span!.chapterId).toBe('ch2');
  });

  it('does not send a span that is a single scroll', () => {
    const { state } = scroll(emptySpanState(), 1000);
    expect(takeFlush(state, 5000, true).flush).toBeNull();
    expect(closeSpan(state).flush).toBeNull();
    expect(scroll(state, 9000).closed).toBeNull();
  });

  it('flushes on the interval, or when forced, and only when changed', () => {
    let { state } = scroll(emptySpanState(), 1000);
    ({ state } = scroll(state, 1030));
    expect(takeFlush(state, 1030).flush).toBeNull();
    expect(takeFlush(state, 1030, true).flush).not.toBeNull();
    let flush;
    ({ state, flush } = takeFlush(state, 1000 + FLUSH_INTERVAL_SECONDS));
    expect(flush).toMatchObject({ activeSeconds: 30 });
    // Nothing changed since: nothing to send, even forced.
    expect(takeFlush(state, 9999, true).flush).toBeNull();
    ({ state } = scroll(state, 1100));
    expect(closeSpan(state).flush).toMatchObject({ activeSeconds: 100 });
  });

  it('ignores a clock that went backwards by starting fresh', () => {
    let { state } = scroll(emptySpanState(), 1000);
    ({ state } = scroll(state, 900));
    expect(state.span).toMatchObject({ startedAt: 900, activeSeconds: 0 });
  });
});
