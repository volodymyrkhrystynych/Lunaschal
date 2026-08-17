import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import { groupByStatus, queueBreakdown } from '@/lib/jobs';
import { useMasterDetail } from '@/hooks/useMasterDetail';
import { ApplicationDetail } from './ApplicationDetail';
import { Feed } from './Feed';
import { ProfileEditor } from './ProfileEditor';

type Tab = 'feed' | 'pipeline' | 'profile' | 'inbox';

function Pipeline({ onOpen }: { onOpen: (applicationId: string) => void }) {
  const { data: applications } = useQuery({
    queryKey: ['jobs', 'applications'],
    queryFn: () => api.jobs.applications.list(),
  });
  const { data: stats } = useQuery({
    queryKey: ['jobs', 'stats'],
    queryFn: api.jobs.stats,
  });

  const groups = groupByStatus(applications ?? []);
  const { ready, building, failed } = queueBreakdown(applications ?? []);

  return (
    <div className="flex-1 overflow-y-auto min-w-0 space-y-4">
      {/* The desktop half of the split: what the phone queued and the machine
          has already built. This is the list you work through at a keyboard. */}
      {(ready.length > 0 || building.length > 0 || failed.length > 0) && (
        <div className="rounded-lg border border-[var(--color-primary)]/30 bg-[var(--color-primary)]/5 p-3 space-y-2">
          <h3 className="text-xs uppercase tracking-wide text-[var(--color-primary)]">
            Ready to submit ({ready.length})
          </h3>
          {ready.map(application => (
            <div
              key={application.id}
              className="flex items-stretch gap-2 rounded-lg border border-white/10 bg-[var(--color-surface)] hover:border-white/20"
            >
              <button
                onClick={() => onOpen(application.id)}
                className="flex-1 min-w-0 text-left p-3 min-h-[44px]"
              >
                <p className="text-sm font-medium text-[var(--color-text)] truncate">
                  {application.title}
                </p>
                <p className="text-xs text-[var(--color-text-muted)] truncate">
                  {application.company} · resume ready
                </p>
              </button>
              {/* Opening the posting is a plain link — the extension picks the
                  tab up by matching its URL, so nothing has to launch it. */}
              {application.jobUrl && (
                <a
                  href={application.jobUrl}
                  target="_blank"
                  rel="noreferrer noopener"
                  title="Open the posting to apply"
                  className="shrink-0 flex items-center px-3 text-xs text-[var(--color-primary)] hover:underline"
                >
                  Apply ↗
                </a>
              )}
            </div>
          ))}
          {building.length > 0 && (
            <p className="text-xs text-[var(--color-text-muted)]">
              {building.length} more still building in the background.
            </p>
          )}
          {failed.map(application => (
            <button
              key={application.id}
              onClick={() => onOpen(application.id)}
              className="w-full text-left p-3 min-h-[44px] rounded-lg border border-red-500/30 bg-[var(--color-surface)]"
            >
              <p className="text-sm text-[var(--color-text)] truncate">
                {application.title}
              </p>
              <p className="text-xs text-red-400 break-words">
                {application.queueError}
              </p>
            </button>
          ))}
        </div>
      )}

      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {(
            [
              ['Sent', stats.counts.submitted + stats.counts.acknowledged],
              ['Interviews', stats.counts.interview],
              ['Offers', stats.counts.offer],
              ['Rejected', stats.counts.rejected],
            ] as const
          ).map(([label, value]) => (
            <div
              key={label}
              className="p-3 rounded-lg border border-white/10 bg-[var(--color-surface)]"
            >
              <p className="text-2xl font-semibold text-[var(--color-text)]">
                {value}
              </p>
              <p className="text-xs text-[var(--color-text-muted)]">{label}</p>
            </div>
          ))}
        </div>
      )}

      {groups.length === 0 ? (
        <p className="text-sm text-[var(--color-text-muted)]">
          No applications yet. Add a posting, then tailor a resume for it.
        </p>
      ) : (
        groups.map(group => (
          <div key={group.status}>
            <h3 className="text-xs uppercase tracking-wide text-[var(--color-text-muted)] mb-1">
              {group.label} ({group.items.length})
            </h3>
            <div className="space-y-1">
              {group.items.map(application => (
                <button
                  key={application.id}
                  onClick={() => onOpen(application.id)}
                  className="w-full text-left p-3 min-h-[44px] rounded-lg border border-white/10 bg-[var(--color-surface)] hover:border-white/20"
                >
                  <p className="text-sm font-medium text-[var(--color-text)] truncate">
                    {application.title}
                  </p>
                  <p className="text-xs text-[var(--color-text-muted)] truncate">
                    {application.company}
                    {application.appliedAt &&
                      ` · sent ${new Date(application.appliedAt).toLocaleDateString()}`}
                  </p>
                </button>
              ))}
            </div>
          </div>
        ))
      )}
    </div>
  );
}

function Inbox() {
  const queryClient = useQueryClient();
  const { data: unlinked } = useQuery({
    queryKey: ['jobs', 'unlinked'],
    queryFn: api.jobs.linkage.unlinked,
  });

  const link = useMutation({
    mutationFn: ({
      applicationId,
      emailId,
    }: {
      applicationId: string;
      emailId: string;
    }) => api.jobs.linkage.link(applicationId, emailId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
  });

  if (!unlinked?.length) {
    return (
      <p className="text-sm text-[var(--color-text-muted)]">
        Every job email has found its application.
      </p>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto min-w-0 space-y-2">
      <p className="text-xs text-[var(--color-text-muted)]">
        These job emails matched nothing confidently. Linking one also advances
        its application’s status.
      </p>
      {unlinked.map(email => (
        <div
          key={email.id}
          className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3"
        >
          <p className="text-sm text-[var(--color-text)] truncate">
            {email.subject || '(no subject)'}
          </p>
          <p className="text-xs text-[var(--color-text-muted)] mb-2">
            {email.senderEmail} ·{' '}
            {new Date(email.receivedAt).toLocaleDateString()}
          </p>
          {email.suggestions.length === 0 ? (
            <p className="text-xs text-[var(--color-text-muted)]">
              No plausible application.
            </p>
          ) : (
            <div className="space-y-1">
              {email.suggestions.map(suggestion => (
                <button
                  key={suggestion.applicationId}
                  type="button"
                  onClick={() =>
                    link.mutate({
                      applicationId: suggestion.applicationId,
                      emailId: email.id,
                    })
                  }
                  className="w-full text-left min-h-[44px] px-3 py-2 rounded border border-white/20 bg-white/5 hover:bg-white/10"
                >
                  <span className="text-sm text-[var(--color-text)]">
                    {suggestion.title} · {suggestion.company}
                  </span>
                  <span className="block text-xs text-[var(--color-text-muted)]">
                    {suggestion.reasons.join('; ')}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export function Jobs() {
  // Feed first: triage is the most frequent thing done here, and on a phone
  // the default tab is the one you get without a tap.
  const [tab, setTab] = useState<Tab>('feed');
  const [selected, setSelected] = useState<string | null>(null);
  const { showList, showDetail, openDetail, openList } = useMasterDetail();

  const { data: stats } = useQuery({
    queryKey: ['jobs', 'stats'],
    queryFn: api.jobs.stats,
  });

  const open = (applicationId: string) => {
    setSelected(applicationId);
    openDetail();
  };

  const tabs: [Tab, string][] = [
    ['feed', 'Feed'],
    ['pipeline', 'Pipeline'],
    ['profile', 'Profile'],
    [
      'inbox',
      `Inbox${stats?.unlinkedEmails ? ` (${stats.unlinkedEmails})` : ''}`,
    ],
  ];

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      {showList && (
        <div className="flex items-center gap-3 mb-4 flex-wrap">
          <h1 className="text-2xl font-semibold text-[var(--color-text)]">
            Jobs
          </h1>
          <div className="flex gap-1 flex-wrap">
            {tabs.map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setTab(key)}
                className={`px-3 min-h-[44px] md:min-h-0 md:py-1.5 rounded text-sm transition-colors ${
                  tab === key
                    ? 'bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40'
                    : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="flex-1 flex gap-4 overflow-hidden">
        {showList && (
          <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
            {tab === 'feed' && <Feed />}
            {tab === 'pipeline' && <Pipeline onOpen={open} />}
            {tab === 'profile' && <ProfileEditor />}
            {tab === 'inbox' && <Inbox />}
          </div>
        )}

        {selected && showDetail && (
          <div className="flex-1 flex flex-col min-w-0 overflow-hidden md:max-w-[55%] md:border-l md:border-white/10">
            <ApplicationDetail
              applicationId={selected}
              onBack={() => {
                openList();
                setSelected(null);
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
