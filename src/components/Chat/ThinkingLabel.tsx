import { useEffect, useRef, useState } from 'react';

// Deliberately vague about *what* is happening: the label is running before the
// delegate decision comes back, so it can't yet honestly say "searching". Once
// there is something specific to report, the step list says it instead.
const WORDS = [
  'Thinking',
  'Pondering',
  'Mulling',
  'Considering',
  'Working it out',
  'Turning it over',
];

const TYPE_MS = 55;
const ERASE_MS = 28;
// Long enough to actually read the word before it starts disappearing.
const HOLD_MS = 1400;

/**
 * A cycling, self-typing status label for the gap before the first token.
 *
 * Replaces a static "Thinking..." that sat unchanged for the 5-30s a local
 * 26B takes to answer, where a frozen string reads as a hung request. Motion
 * that changes is the signal — a spinner alone doesn't distinguish "working"
 * from "stuck".
 *
 * Honours `prefers-reduced-motion` by falling back to one static word: this is
 * decorative text animation, exactly what that query is for.
 */
export function ThinkingLabel() {
  const [text, setText] = useState(WORDS[0]);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    const reduced =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced) return;

    let word = 0;
    let chars = WORDS[0].length;
    let erasing = true;
    let cancelled = false;

    const tick = () => {
      if (cancelled) return;
      let delay: number;
      if (erasing) {
        chars -= 1;
        if (chars <= 0) {
          erasing = false;
          word = (word + 1) % WORDS.length;
          chars = 0;
        }
        delay = ERASE_MS;
      } else {
        chars += 1;
        if (chars >= WORDS[word].length) {
          chars = WORDS[word].length;
          erasing = true;
          delay = HOLD_MS;
        } else {
          delay = TYPE_MS;
        }
      }
      setText(WORDS[word].slice(0, chars));
      timer.current = setTimeout(tick, delay);
    };

    timer.current = setTimeout(tick, HOLD_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer.current);
    };
  }, []);

  return (
    // aria-live is off and the whole thing is aria-hidden: a label retyping
    // itself every 30ms would be a screen-reader firehose. The status is
    // announced once, statically, by the sibling below.
    <span className="inline-flex items-baseline">
      <span aria-hidden>{text}</span>
      <span aria-hidden className="ml-px animate-pulse">
        ▍
      </span>
      <span className="sr-only">Thinking</span>
    </span>
  );
}
