import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import type { EmailMessage } from '../../hooks/api';
import { formatEmailDate } from '../../lib/email';

function StatTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="p-4 rounded-lg border border-white/10 bg-[var(--color-surface)]">
      <p className="text-2xl font-semibold text-[var(--color-text)]">{value}</p>
      <p className="text-sm text-[var(--color-text-muted)]">{label}</p>
    </div>
  );
}

export function JobDashboard({
  onSelect,
}: {
  onSelect: (email: EmailMessage) => void;
}) {
  const { data: stats, isLoading } = useQuery({
    queryKey: ['email', 'stats'],
    queryFn: api.email.stats,
  });

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
        Loading…
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto min-w-0">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        <StatTile label="Applications Sent" value={stats?.sentCount ?? 0} />
        <StatTile label="Rejections" value={stats?.rejectionCount ?? 0} />
        <StatTile
          label="Interview Next Steps"
          value={stats?.interviewNextStepCount ?? 0}
        />
        <StatTile label="Other Updates" value={stats?.otherUpdateCount ?? 0} />
      </div>

      <h3 className="text-sm font-medium text-[var(--color-text-muted)] mb-2">
        Next Steps
      </h3>
      {!stats?.nextSteps.length ? (
        <p className="text-sm text-[var(--color-text-muted)]">
          No pending next steps.
        </p>
      ) : (
        <div className="space-y-1">
          {stats.nextSteps.map(email => (
            <button
              key={email.id}
              onClick={() => onSelect(email)}
              className="w-full text-left p-3 rounded-lg border border-white/10 bg-[var(--color-surface)] hover:border-white/20 transition-colors"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-sm text-[var(--color-text)] truncate">
                  {email.sender || email.senderEmail}
                </span>
                <span className="text-xs text-[var(--color-text-muted)] shrink-0">
                  {formatEmailDate(email.receivedAt)}
                </span>
              </div>
              <p className="text-sm text-[var(--color-text)] truncate">
                {email.subject || '(no subject)'}
              </p>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
