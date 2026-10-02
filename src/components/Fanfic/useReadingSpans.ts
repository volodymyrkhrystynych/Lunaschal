import { useEffect, useRef, type RefObject } from 'react';
import { scrollFraction } from '../../lib/fanficBookmarks';
import {
  closeSpan,
  emptySpanState,
  recordScroll,
  takeFlush,
  type ReadingSpan,
} from '../../lib/readingSpans';
import { ulid } from '../../lib/ulid';
import { useFanficReadingSpan } from '../../offline/mutationDefaults';

/** A scroll counts as the user's own only this soon after a wheel, touch,
 *  key or pointer press. The reader scrolls itself too — back to the top on a
 *  chapter change, to a bookmark on restore — and those prove nothing. */
export const INPUT_WINDOW_MS = 3000;

const USER_INPUTS = ['wheel', 'touchmove', 'keydown', 'pointerdown'] as const;

/**
 * Record when a chapter is open and being scrolled, as reading spans the
 * briefing's day reconstruction can cite (see src/lib/readingSpans.ts).
 * `chapterId` null (a PDF, or no chapter yet) records nothing.
 */
export function useReadingSpans(
  ficId: string,
  chapterId: string | null,
  contentRef: RefObject<HTMLElement | null>
) {
  const { mutate } = useFanficReadingSpan();
  const send = useRef(mutate);
  send.current = mutate;
  const state = useRef(emptySpanState());
  const lastInput = useRef(0);

  useEffect(() => {
    const onInput = () => {
      lastInput.current = Date.now();
    };
    for (const type of USER_INPUTS) {
      window.addEventListener(type, onInput, { capture: true, passive: true });
    }
    return () => {
      for (const type of USER_INPUTS) {
        window.removeEventListener(type, onInput, { capture: true });
      }
    };
  }, []);

  useEffect(() => {
    if (!chapterId) return;
    const flush = (span: ReadingSpan | null) => {
      if (span) send.current(span);
    };
    // The content box is mounted and unmounted with the master-detail layout,
    // so listen at the document and match the target rather than binding to
    // whichever element the ref held when this effect ran.
    const onScroll = (event: Event) => {
      const el = contentRef.current;
      if (!el || event.target !== el || document.hidden) return;
      const nowMs = Date.now();
      if (nowMs - lastInput.current > INPUT_WINDOW_MS) return;
      const now = Math.floor(nowMs / 1000);
      const recorded = recordScroll(state.current, {
        now,
        fraction: scrollFraction(
          el.scrollTop,
          el.scrollHeight,
          el.clientHeight
        ),
        ficId,
        chapterId,
        newId: ulid,
      });
      flush(recorded.closed);
      const due = takeFlush(recorded.state, now);
      state.current = due.state;
      flush(due.flush);
    };
    const onHide = () => {
      if (!document.hidden) return;
      const due = takeFlush(state.current, Math.floor(Date.now() / 1000), true);
      state.current = due.state;
      flush(due.flush);
    };
    document.addEventListener('scroll', onScroll, {
      capture: true,
      passive: true,
    });
    document.addEventListener('visibilitychange', onHide);
    return () => {
      document.removeEventListener('scroll', onScroll, { capture: true });
      document.removeEventListener('visibilitychange', onHide);
      const closed = closeSpan(state.current);
      state.current = closed.state;
      flush(closed.flush);
    };
  }, [ficId, chapterId, contentRef]);
}
