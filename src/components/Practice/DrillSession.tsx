import { useEffect, useRef, useState } from 'react';
import type { ChangeEvent } from 'react';
import type { PracticeSpeedDrill } from '../../hooks/api';
import { diffTyped, computeStats, type TypingStats } from '../../lib/practice';
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
  const [typed, setTyped] = useState('');
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const keystrokes = useRef(0);
  const mistakes = useRef(0);
  const startedAt = useRef<number | null>(null);
  const finished = useRef(false);
  const code = snippet.code;

  useEffect(() => {
    setTyped('');
    keystrokes.current = 0;
    mistakes.current = 0;
    startedAt.current = null;
    finished.current = false;
    inputRef.current?.focus();
  }, [snippet.id]);

  function handleChange(e: ChangeEvent<HTMLTextAreaElement>) {
    if (finished.current) return;
    if (startedAt.current === null) startedAt.current = performance.now();

    const raw = e.target.value;
    if (raw.length > typed.length) {
      for (let i = typed.length; i < raw.length && i < code.length; i++) {
        keystrokes.current += 1;
        if (raw[i] !== code[i]) mistakes.current += 1;
      }
    }

    const next = raw.slice(0, code.length);
    setTyped(next);

    if (next.length === code.length) {
      finished.current = true;
      const elapsedMs =
        startedAt.current !== null ? performance.now() - startedAt.current : 0;
      onComplete(
        computeStats({
          targetLength: code.length,
          keystrokes: keystrokes.current,
          mistakes: mistakes.current,
          elapsedMs,
        })
      );
    }
  }

  const statuses = diffTyped(code, typed);

  return (
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
    </div>
  );
}
