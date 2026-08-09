import { useEffect, useRef, useState } from 'react';
import type { PracticeBlindDrill, PracticeRecallResult } from '../../hooks/api';
import { ExplanationPanel, ExplanationWithheld } from './ExplanationPanel';
import { TypingCanvas } from './TypingCanvas';

interface Props {
  snippet: PracticeBlindDrill;
  result: PracticeRecallResult | null;
  grading: boolean;
  error: boolean;
  onSubmit: (submitted: string) => void;
  onNext: () => void;
}

const VERDICT_LABEL: Record<PracticeRecallResult['verdict'], string> = {
  correct: 'Correct',
  partial: 'Nearly',
  wrong: 'Not quite',
};

const VERDICT_CLASS: Record<PracticeRecallResult['verdict'], string> = {
  correct: 'text-emerald-400',
  partial: 'text-amber-400',
  wrong: 'text-rose-400',
};

// The blind half of the drill: the task, an empty box, and no sight of the
// answer until it has been graded.
//
// The box is a plain <textarea> rather than the CodeMirror editor the speed
// drill displays through. An editor's bracket-closing, autocomplete and
// auto-indent would write parts of the answer for you, which is the one thing a
// recall test cannot afford — and there is nothing here to syntax-highlight
// until you've written it.
export function RecallSession({
  snippet,
  result,
  grading,
  error,
  onSubmit,
  onNext,
}: Props) {
  const [typed, setTyped] = useState('');
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const nextRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    setTyped('');
    inputRef.current?.focus();
  }, [snippet.id]);

  // Focus follows the one thing left to do, so the whole drill stays on the
  // keyboard — this app is used on a handheld with no usable mouse.
  useEffect(() => {
    if (result) nextRef.current?.focus();
  }, [result]);

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter is a newline here (snippets are multi-line), so submitting takes a
    // modifier — the same bargain every code box in the app makes.
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      if (!grading && typed.trim()) onSubmit(typed);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between text-sm text-[var(--color-text-muted)]">
        <span className="inline-flex items-center gap-2">
          <span className="px-1.5 py-0.5 rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] text-xs font-medium uppercase tracking-wide">
            From memory
          </span>
          {snippet.title}
        </span>
        <span className="capitalize">
          {snippet.language} · {snippet.category}
        </span>
      </div>

      <p className="text-[var(--color-text)] bg-[var(--color-surface)] border border-white/10 rounded-lg p-4">
        {snippet.prompt}
      </p>

      <textarea
        ref={inputRef}
        value={typed}
        onChange={e => setTyped(e.target.value)}
        onKeyDown={handleKeyDown}
        readOnly={!!result}
        rows={8}
        spellCheck={false}
        autoComplete="off"
        autoCapitalize="off"
        autoCorrect="off"
        placeholder="Write it from memory…"
        aria-label={`Write the ${snippet.title} snippet from memory`}
        className="w-full rounded-lg border border-white/10 bg-[var(--color-bg)] p-3 font-mono text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)] resize-y"
      />

      {!result && (
        <div className="flex items-center gap-3">
          <button
            onClick={() => onSubmit(typed)}
            disabled={grading || !typed.trim()}
            className="px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors font-medium disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {grading ? 'Grading…' : 'Check'}
          </button>
          <span className="text-xs text-[var(--color-text-muted)]">
            Graded on whether it works, not on how it's spaced.
          </span>
        </div>
      )}

      {error && !result && (
        <div className="text-sm text-rose-400">
          Grading failed. Try again — nothing has been recorded.
        </div>
      )}

      {/* Sits where the speed drill's panel sits, so the row stays put from one
          drill to the next; the explanation itself arrives with the grade. */}
      {!result && <ExplanationWithheld />}

      {result && (
        <div className="flex flex-col gap-3">
          <div className="flex items-baseline gap-2">
            <span
              className={`text-lg font-semibold ${VERDICT_CLASS[result.verdict]}`}
            >
              {VERDICT_LABEL[result.verdict]}
            </span>
            {result.gradedBy === 'fallback' && (
              // Said out loud, because this verdict came from comparing text:
              // a correct answer written differently is marked wrong by it, and
              // that is worth knowing before believing it.
              <span className="text-xs text-[var(--color-text-muted)]">
                graded offline by text comparison — llama-server was unreachable
              </span>
            )}
          </div>
          <p className="text-sm text-[var(--color-text)]">{result.feedback}</p>
          <div>
            <div className="text-xs uppercase tracking-wide text-[var(--color-text-muted)] mb-1">
              Reference answer
            </div>
            <TypingCanvas
              language={snippet.language}
              code={result.reference}
              statuses={[]}
              caret={false}
            />
          </div>
          {/* Open, unlike the speed drill's: there is nothing left to type, and
              this is the part of the drill worth reading. */}
          <ExplanationPanel explanation={result.explanation} defaultOpen />
          <button
            ref={nextRef}
            onClick={onNext}
            className="self-start px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors font-medium"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
