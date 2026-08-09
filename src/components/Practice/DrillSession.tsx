import { useEffect, useMemo, useRef, useState } from 'react';
import type { ChangeEvent } from 'react';
import type { PracticeSpeedDrill } from '../../hooks/api';
import {
  acceptsChar,
  computeStats,
  diffTyped,
  nextRequired,
  previousRequired,
  requiredMask,
  type TypingStats,
} from '../../lib/practice';
import { ExplanationPanel } from './ExplanationPanel';
import { TypingCanvas } from './TypingCanvas';

interface Props {
  snippet: PracticeSpeedDrill;
  onComplete: (stats: TypingStats) => void;
}

// Keystrokes are captured by a plain, visually-hidden <textarea> rather than
// CodeMirror's own edit model — a normal form control naturally handles
// backspace, mobile/IME keyboards, paste-blocking, and (being a textarea
// rather than a single-line input) real newlines, which most snippets in the
// bank contain. CodeMirror (in TypingCanvas) is used purely for the
// highlighted, read-only display.
export function DrillSession({ snippet, onComplete }: Props) {
  const code = snippet.code;
  const mask = useMemo(() => requiredMask(code), [code]);
  // Whatever leads the snippet in is already on screen before the first
  // keystroke, so the caret starts on a character worth typing.
  const [typed, setTyped] = useState(() =>
    code.slice(0, nextRequired(mask, 0))
  );
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const keystrokes = useRef(0);
  const mistakes = useRef(0);
  const startedAt = useRef<number | null>(null);
  const finished = useRef(false);

  useEffect(() => {
    setTyped(code.slice(0, nextRequired(mask, 0)));
    keystrokes.current = 0;
    mistakes.current = 0;
    startedAt.current = null;
    finished.current = false;
    inputRef.current?.focus();
    // `code`/`mask` are derived from the snippet, so its id is the whole change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snippet.id]);

  function handleChange(e: ChangeEvent<HTMLTextAreaElement>) {
    if (finished.current) return;
    const raw = e.target.value;

    // A backspace steps back over the run the drill filled in, to the last
    // character the writer produced. Keystrokes already spent are not refunded:
    // fixing a typo before submitting must not read as having typed it right.
    if (raw.length <= typed.length) {
      setTyped(code.slice(0, previousRequired(mask, typed.length)));
      return;
    }

    if (startedAt.current === null) startedAt.current = performance.now();

    // One character at a time, because each accepted keystroke can pull in a
    // filled-in run behind it and move where the next one lands.
    let next = typed;
    for (
      let i = typed.length;
      i < raw.length && next.length < code.length;
      i++
    ) {
      const pos = next.length;
      keystrokes.current += 1;
      if (acceptsChar(code, pos, raw[i])) {
        // The target's own character, not the keystroke: a space answering a
        // required newline is right, and should render as right.
        next += code[pos];
      } else {
        mistakes.current += 1;
        next += raw[i];
      }
      next += code.slice(next.length, nextRequired(mask, next.length));
    }
    setTyped(next);

    if (next.length === code.length) {
      finished.current = true;
      const elapsedMs =
        startedAt.current !== null ? performance.now() - startedAt.current : 0;
      onComplete(
        computeStats({
          // The whole snippet, including what was filled in — wpm measures how
          // fast this snippet gets produced, and every attempt already recorded
          // measured it that way. Narrowing it to typed characters would make
          // every `best_wpm` in the table unbeatable.
          targetLength: code.length,
          keystrokes: keystrokes.current,
          mistakes: mistakes.current,
          elapsedMs,
        })
      );
    }
  }

  const statuses = diffTyped(code, typed);

  // The click-to-focus wrapper deliberately stops at the typing area, so a click
  // on the explanation's toggle is not also a click that returns focus to the
  // hidden textarea — expanding it would otherwise scroll the drill away and put
  // the caret straight back in the snippet.
  return (
    <div className="flex flex-col gap-3">
      <div
        className="flex flex-col gap-3"
        onClick={() => inputRef.current?.focus()}
      >
        <div className="flex items-center justify-between text-sm text-[var(--color-text-muted)]">
          <span>{snippet.title}</span>
          <span className="capitalize">
            {snippet.language} · {snippet.category}
          </span>
        </div>
        <TypingCanvas
          language={snippet.language}
          code={code}
          statuses={statuses}
        />
        <textarea
          ref={inputRef}
          value={typed}
          onChange={handleChange}
          onPaste={e => e.preventDefault()}
          autoComplete="off"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          className="sr-only"
          aria-label={`Type the ${snippet.title} snippet`}
        />
        <p className="text-xs text-[var(--color-text-muted)]">
          Indentation and line breaks are filled in for you — type only the
          spacing that keeps two words apart.
        </p>
      </div>

      <ExplanationPanel explanation={snippet.explanation} />
    </div>
  );
}
