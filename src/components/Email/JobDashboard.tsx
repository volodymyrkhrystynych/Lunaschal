import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { STATUS_LABELS } from '@/lib/jobs';

function StatTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="p-4 rounded-lg border border-white/10 bg-[var(--color-surface)]">
      <p className="text-2xl font-semibold text-[var(--color-text)]">{value}</p>
      <p className="text-sm text-[var(--color-text-muted)]">{label}</p>
    </div>
  );
}

/**
 * The job search as seen from the Email tab.
 *
 * This used to count *emails* by their classifier sub-status, which was the
 * only thing available before applications existed: three rejections about one
 * job read as three rejections. It now counts applications, and the emails are
 * what moved them — so the number on screen is the number of jobs.
 *
 * The pipeline itself lives in the Jobs tab; this stays because the mail is
 * where the outcomes arrive, and it surfaces the one thing that needs a human:
 * job mail that matched no application.
 */
export function JobDashboard() {
  const { data: stats, isLoading } = useQuery({
    queryKey: ['jobs', 'stats'],
    queryFn: api.jobs.stats,
  });

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
        Loading…
      </div>
    );
  }

  const counts = stats?.counts;

  return (
    <div className="flex-1 overflow-y-auto min-w-0">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        <StatTile
          label="Applications Sent"
          value={(counts?.submitted ?? 0) + (counts?.acknowledged ?? 0)}
        />
        <StatTile label="Interviews" value={counts?.interview ?? 0} />
        <StatTile label="Offers" value={counts?.offer ?? 0} />
        <StatTile label="Rejections" value={counts?.rejected ?? 0} />
      </div>

      {(stats?.unlinkedEmails ?? 0) > 0 && (
        <p className="mb-4 text-sm text-amber-300">
          {stats?.unlinkedEmails} job email
          {stats?.unlinkedEmails === 1 ? '' : 's'} matched no application — link
          them from the Jobs tab’s Inbox.
        </p>
      )}

      <h3 className="text-sm font-medium text-[var(--color-text-muted)] mb-2">
        In Flight
      </h3>
      {!stats?.active.length ? (
        <p className="text-sm text-[var(--color-text-muted)]">
          Nothing waiting on a reply.
        </p>
      ) : (
        <div className="space-y-1">
          {stats.active.map(application => (
            <div
              key={application.id}
              className="p-3 rounded-lg border border-white/10 bg-[var(--color-surface)]"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-sm text-[var(--color-text)] truncate">
                  {application.company}
                </span>
                <span className="text-xs text-[var(--color-text-muted)] shrink-0">
                  {STATUS_LABELS[application.status]}
                </span>
              </div>
              <p className="text-sm text-[var(--color-text)] truncate">
                {application.title}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
