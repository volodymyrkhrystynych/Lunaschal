import type { ClaimCoverage } from '../../hooks/api';

interface Props {
  coverage: ClaimCoverage;
  normalizedAnswer?: string;
}

export function CoverageResult({ coverage, normalizedAnswer }: Props) {
  return (
    <div className="text-left space-y-3">
      {coverage.summary && (
        <p className="text-sm text-[var(--color-text)]">{coverage.summary}</p>
      )}
      {coverage.claims.length > 0 && (
        <ul className="space-y-1.5">
          {coverage.claims.map((c, i) => (
            <li key={i} className="flex items-start gap-2 text-sm">
              <span className={c.covered ? 'text-green-400' : 'text-red-400'}>
                {c.covered ? '✓' : '✗'}
              </span>
              <span className={c.covered ? 'text-[var(--color-text)]' : 'text-[var(--color-text-muted)]'}>
                {c.text}
                {!c.essential && <span className="ml-1.5 text-xs opacity-60">nuance</span>}
                {c.note && <span className="ml-1.5 text-xs text-orange-300">({c.note})</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
      {coverage.gated && (
        <p className="text-xs text-[var(--color-text-muted)]">
          Quick check: your answer didn't resemble the stored one, so no detailed comparison was run.
        </p>
      )}
      {normalizedAnswer && (
        <p className="text-xs text-[var(--color-text-muted)]">
          Graded as: "{normalizedAnswer}"
        </p>
      )}
    </div>
  );
}
