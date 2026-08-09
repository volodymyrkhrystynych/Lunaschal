// Pure typing-drill logic for the Practice tab: per-character diffing against
// a target snippet, and the wpm/accuracy math. Kept out of the component so
// it's testable in the node environment (no jsdom, no keyboard events).

export type CharStatus = 'correct' | 'incorrect' | 'pending';

const WORD = /[A-Za-z0-9_$]/;
const SPACE = /\s/;

// Which positions of the snippet the writer has to produce, and which the drill
// fills in for them.
//
// A run of whitespace is load-bearing only when deleting it outright would fuse
// two words into one — `const item` needs its space, `a = 1` and `() => {` do
// not, and neither does an indent or a line break. The drill types the rest in
// as the caret reaches it, so accuracy measures the characters that decide what
// the program does rather than how it was laid out. That matters here because
// accuracy is the gate on the blind unlock (backend/practice/modes.py), and a
// mis-indent is not evidence that the syntax isn't known.
//
// Only the *first* character of a load-bearing run is required; `const  item`
// asks for one space, not two. Which character it is doesn't matter — see
// `acceptsChar` — because nine snippets in the bank break an HTML attribute
// onto its own line, where the required separator happens to be a newline.
export function requiredMask(target: string): boolean[] {
  const mask = target.split('').map(ch => !SPACE.test(ch));
  for (const run of target.matchAll(/\s+/g)) {
    const start = run.index;
    const end = start + run[0].length;
    const prev = start > 0 ? target[start - 1] : '';
    const next = end < target.length ? target[end] : '';
    if (prev && next && WORD.test(prev) && WORD.test(next)) mask[start] = true;
  }
  return mask;
}

// Whether a keystroke satisfies the target position. Exact, except that any
// whitespace answers a required whitespace position: that position exists to
// keep two words apart, and a space does that as well as the newline the
// reference happens to use. Holding out for the newline would teach the bank's
// line wrapping, which is not what is being drilled.
export function acceptsChar(
  target: string,
  index: number,
  ch: string
): boolean {
  if (ch === target[index]) return true;
  return SPACE.test(target[index]) && SPACE.test(ch);
}

/** The next position the writer must produce, skipping what is filled in. */
export function nextRequired(mask: boolean[], from: number): number {
  let i = from;
  while (i < mask.length && !mask[i]) i += 1;
  return i;
}

/**
 * Where a backspace lands: the last position the writer produced themselves.
 *
 * Stepping over the filled-in run rather than into it is what keeps one press
 * equal to one character. Landing inside an indent would delete something the
 * writer never typed and then have it reappear on the next keystroke.
 */
export function previousRequired(mask: boolean[], before: number): number {
  for (let i = before - 1; i >= 0; i -= 1) if (mask[i]) return i;
  return 0;
}

// Input is capped at the target's length by the caller, so there's no "typed
// past the end" case to represent here.
export function diffTyped(target: string, typed: string): CharStatus[] {
  return target.split('').map((ch, i) => {
    if (i >= typed.length) return 'pending';
    return typed[i] === ch ? 'correct' : 'incorrect';
  });
}

export interface TypingStats {
  wpm: number;
  accuracy: number;
  errorCount: number;
}

// `keystrokes`/`mistakes` are tracked by the caller at the moment each
// character is typed, independent of later backspacing — otherwise fixing
// every typo before submitting would always read as 100% accuracy, which
// defeats the point of measuring correctness under speed.
export function computeStats(params: {
  targetLength: number;
  keystrokes: number;
  mistakes: number;
  elapsedMs: number;
}): TypingStats {
  const { targetLength, keystrokes, mistakes, elapsedMs } = params;
  const minutes = elapsedMs / 60000;
  const wpm = minutes > 0 ? targetLength / 5 / minutes : 0;
  const accuracy =
    keystrokes > 0 ? ((keystrokes - mistakes) / keystrokes) * 100 : 100;
  return { wpm, accuracy, errorCount: mistakes };
}
