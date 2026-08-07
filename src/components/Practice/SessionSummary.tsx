import type { PracticeSnippet } from '../../hooks/api';
import type { TypingStats } from '../../lib/practice';

export interface DrillResult {
  snippet: PracticeSnippet;
  stats: TypingStats;
  rating: string;
}

interface Props {
  results: DrillResult[];
  onRestart: () => void;
}

export function SessionSummary({ results, onRestart }: Props) {
  const avgWpm = results.length
    ? results.reduce((sum, r) => sum + r.stats.wpm, 0) / results.length
    : 0;
  const avgAccuracy = results.length
    ? results.reduce((sum, r) => sum + r.stats.accuracy, 0) / results.length
    : 0;

  return (
    <div className="bg-[var(--color-surface)] rounded-lg border border-white/10 p-6 flex flex-col gap-4">
      <h2 className="text-lg font-semibold text-[var(--color-text)]">
        Session complete
      </h2>
      <div className="flex gap-8">
        <div>
          <div className="text-xs text-[var(--color-text-muted)] uppercase tracking-wide">
            Avg WPM
          </div>
          <div className="text-2xl font-semibold text-[var(--color-text)]">
            {avgWpm.toFixed(0)}
          </div>
        </div>
        <div>
          <div className="text-xs text-[var(--color-text-muted)] uppercase tracking-wide">
            Avg accuracy
          </div>
          <div className="text-2xl font-semibold text-[var(--color-text)]">
            {avgAccuracy.toFixed(0)}%
          </div>
        </div>
      </div>
      <ul className="flex flex-col gap-1 text-sm">
        {results.map((r, i) => (
          <li
            key={`${r.snippet.id}-${i}`}
            className="flex justify-between border-b border-white/5 py-1 last:border-none"
          >
            <span className="text-[var(--color-text)]">{r.snippet.title}</span>
            <span className="text-[var(--color-text-muted)]">
              {r.stats.wpm.toFixed(0)} wpm · {r.stats.accuracy.toFixed(0)}% ·{' '}
              {r.rating}
            </span>
          </li>
        ))}
      </ul>
      <button
        onClick={onRestart}
        className="self-start px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors font-medium"
      >
        Run another session
      </button>
    </div>
  );
}
