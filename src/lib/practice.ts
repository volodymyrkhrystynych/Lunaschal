// Pure typing-drill logic for the Practice tab: per-character diffing against
// a target snippet, and the wpm/accuracy math. Kept out of the component so
// it's testable in the node environment (no jsdom, no keyboard events).

export type CharStatus = 'correct' | 'incorrect' | 'pending';

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
