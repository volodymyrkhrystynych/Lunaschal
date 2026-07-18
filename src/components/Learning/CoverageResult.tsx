import type { ClaimCoverage } from '../../hooks/api';

interface Props {
  coverage: ClaimCoverage;
  normalizedAnswer?: string;
  fontSize?: number;
}

// Matches Tailwind's text-sm at the default card font size (see
// LEARNING_CARD_FONT_SIZE_DEFAULT in lib/fontSize.ts).
export const COVERAGE_DEFAULT_FONT_SIZE = 14;

export function CoverageResult({
  coverage,
  normalizedAnswer,
  fontSize,
}: Props) {
  return (
    <div
      className="text-left space-y-3"
      style={{ fontSize: `${fontSize ?? COVERAGE_DEFAULT_FONT_SIZE}px` }}
    >
      {coverage.summary && (
        <p className="text-[var(--color-text)]">{coverage.summary}</p>
      )}
      {coverage.claims.length > 0 && (
        <ul className="space-y-1.5">
          {coverage.claims.map((c, i) => (
            <li key={i} className="flex items-start gap-2">
              <span className={c.covered ? 'text-green-400' : 'text-red-400'}>
                {c.covered ? '✓' : '✗'}
              </span>
              <span
                className={
                  c.covered
                    ? 'text-[var(--color-text)]'
                    : 'text-[var(--color-text-muted)]'
                }
              >
                {c.text}
                {!c.essential && (
                  <span className="ml-1.5 text-[0.86em] opacity-60">
                    nuance
                  </span>
                )}
                {c.note && (
                  <span className="ml-1.5 text-[0.86em] text-orange-300">
                    ({c.note})
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
      {coverage.gated && (
        <p className="text-[0.86em] text-[var(--color-text-muted)]">
          Quick check: your answer didn't resemble the stored one, so no
          detailed comparison was run.
        </p>
      )}
      {normalizedAnswer && (
        <p className="text-[0.86em] text-[var(--color-text-muted)]">
          Graded as: "{normalizedAnswer}"
        </p>
      )}
    </div>
  );
}
