import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';

// Collapsed to a single button by default and opened as an overlay, so the
// numbers sit in the filter row without pushing the drill down the screen —
// this app is used on a handheld, where a permanent stats block costs more
// vertical space than it is worth between drills.
export function StatsPanel() {
  const [open, setOpen] = useState(false);
  const { data } = useQuery({
    queryKey: ['practice', 'stats'],
    queryFn: api.practice.stats,
  });

  // The persisted cache is restored before any refetch and can be up to 30 days
  // old, so this renders against shapes written by an earlier version of the
  // app. `recall` arrived after those rows did, and reading straight through it
  // threw during render — which, with no error boundary, blanked the whole
  // Practice tab rather than losing one line of stats. Bumping PERSIST_BUSTER
  // discards the stale rows; this keeps the next such addition cheap.
  const recall = data?.recall ?? { attempts: 0, passes: 0, passRate: null };
  const byLanguage = data?.byLanguage ?? {};

  if (!data || (data.totalAttempts === 0 && recall.attempts === 0)) return null;

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        className="px-3 py-2 rounded bg-[var(--color-bg)] border border-white/10 text-[var(--color-text-muted)] text-sm hover:text-[var(--color-text)] transition-colors"
      >
        Progress
      </button>

      {open && (
        <div className="absolute right-0 z-10 mt-2 w-72 bg-[var(--color-surface)] rounded-lg border border-white/10 p-4 flex flex-col gap-3 shadow-lg">
          <h2 className="text-sm font-semibold text-[var(--color-text)]">
            Overall progress
          </h2>
          <div className="text-sm text-[var(--color-text-muted)]">
            {data.totalAttempts} attempts · {data.avgWpm?.toFixed(0) ?? 0} avg
            wpm · {data.avgAccuracy?.toFixed(0) ?? 0}% avg accuracy
          </div>
          {/* Recall is reported on its own line, never folded into the averages
              above: those measure typing, this measures knowing it. */}
          {recall.attempts > 0 && (
            <div className="text-sm text-[var(--color-text-muted)]">
              {recall.attempts} from memory · {recall.passRate?.toFixed(0) ?? 0}
              % recalled
            </div>
          )}
          <div className="grid grid-cols-2 gap-2">
            {Object.entries(byLanguage).map(([lang, s]) => (
              <div
                key={lang}
                className="rounded-md bg-white/5 p-2 flex flex-col gap-0.5"
              >
                <div className="text-xs text-[var(--color-text-muted)] capitalize">
                  {lang}
                </div>
                <div className="text-sm text-[var(--color-text)]">
                  {s.attempts} · {s.avgWpm?.toFixed(0) ?? 0}wpm
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
