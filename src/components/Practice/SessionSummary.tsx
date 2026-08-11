import type {
  PracticeBlindDrill,
  PracticeRecallResult,
  PracticeSpeedDrill,
} from '../../hooks/api';
import type { TypingStats } from '../../lib/practice';

// A session mixes the two drills, so a result is one of two shapes. They are
// not averaged together: wpm over a snippet written from memory would be
// measuring how fast it was recalled, which is not a typing speed.
export interface SpeedResult {
  mode: 'speed';
  snippet: PracticeSpeedDrill;
  stats: TypingStats;
  rating: string;
}

export interface RecallResult {
  mode: 'blind';
  snippet: PracticeBlindDrill;
  verdict: PracticeRecallResult['verdict'];
  passed: boolean;
}

export type DrillResult = SpeedResult | RecallResult;

interface Props {
  results: DrillResult[];
  onRestart: () => void;
}

const VERDICT_LABEL: Record<PracticeRecallResult['verdict'], string> = {
  correct: 'recalled',
  partial: 'nearly',
  wrong: 'missed',
};

export function SessionSummary({ results, onRestart }: Props) {
  const typed = results.filter((r): r is SpeedResult => r.mode === 'speed');
  const recalled = results.filter((r): r is RecallResult => r.mode === 'blind');

  const avgWpm = typed.length
    ? typed.reduce((sum, r) => sum + r.stats.wpm, 0) / typed.length
    : 0;
  const avgAccuracy = typed.length
    ? typed.reduce((sum, r) => sum + r.stats.accuracy, 0) / typed.length
    : 0;
  const recallPasses = recalled.filter(r => r.passed).length;

  return (
    <div className="bg-[var(--color-surface)] rounded-lg border border-white/10 p-6 flex flex-col gap-4">
      <h2 className="text-lg font-semibold text-[var(--color-text)]">
        Session complete
      </h2>
      <div className="flex gap-8">
        {typed.length > 0 && (
          <>
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
          </>
        )}
        {recalled.length > 0 && (
          <div>
            <div className="text-xs text-[var(--color-text-muted)] uppercase tracking-wide">
              Recalled
            </div>
            <div className="text-2xl font-semibold text-[var(--color-text)]">
              {recallPasses}/{recalled.length}
            </div>
          </div>
        )}
      </div>
      <ul className="flex flex-col gap-1 text-sm">
        {results.map((r, i) => (
          <li
            key={`${r.snippet.id}-${i}`}
            className="flex justify-between border-b border-white/5 py-1 last:border-none"
          >
            <span className="text-[var(--color-text)]">{r.snippet.title}</span>
            <span className="text-[var(--color-text-muted)]">
              {r.mode === 'speed'
                ? `${r.stats.wpm.toFixed(0)} wpm · ${r.stats.accuracy.toFixed(0)}% · ${r.rating}`
                : `from memory · ${VERDICT_LABEL[r.verdict]}`}
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
