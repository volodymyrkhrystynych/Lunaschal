import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import { groupByStatus, queueBreakdown } from '@/lib/jobs';
import { useMasterDetail } from '@/hooks/useMasterDetail';
import { ApplicationDetail } from './ApplicationDetail';
import { Feed } from './Feed';
import { ProfileEditor } from './ProfileEditor';

type Tab = 'feed' | 'pipeline' | 'upskill' | 'profile' | 'inbox';

function Upskill() {
  const local = useMutation({ mutationFn: () => api.jobs.upskill(false) });
  const enriched = useMutation({ mutationFn: () => api.jobs.upskill(true) });
  const plan = enriched.data ?? local.data;
  return (
    <div className="flex-1 overflow-y-auto space-y-3">
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => local.mutate()}
          disabled={local.isPending}
          className="min-h-[44px] px-4 rounded border border-white/20 bg-white/5"
        >
          Analyze recent postings
        </button>
        {plan?.resourcesAvailable && (
          <button
            type="button"
            onClick={() => enriched.mutate()}
            disabled={enriched.isPending}
            className="min-h-[44px] px-4 rounded border border-white/20 bg-white/5"
          >
            Add learning resources
          </button>
        )}
      </div>
      {(local.isError || enriched.isError) && (
        <p className="text-sm text-red-400">
          {((local.error || enriched.error) as Error).message}
        </p>
      )}
      {plan && (
        <>
          <p className="text-xs text-[var(--color-text-muted)]">
            Missing skills across {plan.postings} recent postings. Time
            estimates are rough orientation ranges.
          </p>
          {plan.skills.map(skill => (
            <div
              key={skill.term}
              className="rounded border border-white/10 bg-[var(--color-surface)] p-3"
            >
              <div className="flex justify-between gap-2">
                <p className="text-sm font-medium capitalize">{skill.term}</p>
                <span className="text-xs">
                  {skill.postings}/{skill.ofPostings} postings · ~
                  {skill.estimatedHours}h
                </span>
              </div>
              <div className="h-1.5 bg-white/10 rounded mt-2">
                <div
                  className="h-full bg-[var(--color-primary)] rounded"
                  style={{ width: `${Math.max(3, skill.centrality * 100)}%` }}
                />
              </div>
              {skill.examples.length > 0 && (
                <p className="text-xs text-[var(--color-text-muted)] mt-2">
                  Seen at{' '}
                  {skill.examples.map(x => x.company || x.title).join(', ')}
                </p>
              )}
              {skill.resources.map(resource => (
                <a
                  key={resource.url}
                  href={resource.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="block text-xs text-[var(--color-primary)] mt-1 hover:underline"
                >
                  {resource.title} ↗
                </a>
              ))}
            </div>
          ))}
        </>
      )}
    </div>
  );
}

function Pipeline({ onOpen }: { onOpen: (applicationId: string) => void }) {
  const queryClient = useQueryClient();
  const { data: applications } = useQuery({
    queryKey: ['jobs', 'applications'],
    queryFn: () => api.jobs.applications.list(),
  });
  const { data: stats } = useQuery({
    queryKey: ['jobs', 'stats'],
    queryFn: api.jobs.stats,
  });
  const { data: stale } = useQuery({
    queryKey: ['jobs', 'stale', 10],
    queryFn: () => api.jobs.stale(10),
  });
  const markGhosted = useMutation({
    mutationFn: (id: string) =>
      api.jobs.applications.update(id, { status: 'ghosted' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
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

      {stale && stale.length > 0 && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 space-y-2">
          <h3 className="text-xs uppercase tracking-wide text-amber-300">
            Waiting 10+ days ({stale.length})
          </h3>
          <p className="text-xs text-[var(--color-text-muted)]">
            Open one to draft a follow-up. With no linked reply, it is marked
            ghosted automatically after 60 days.
          </p>
          {stale.map(application => (
            <div
              key={application.id}
              className="flex items-center gap-2 rounded border border-white/10 bg-[var(--color-surface)]"
            >
              <button
                type="button"
                onClick={() => onOpen(application.id)}
                className="flex-1 min-w-0 text-left p-3 min-h-[44px]"
              >
                <span className="block text-sm truncate">
                  {application.title} · {application.company}
                </span>
                <span className="block text-xs text-[var(--color-text-muted)]">
                  {application.daysWaiting} days without a linked reply
                </span>
              </button>
              {application.status !== 'ghosted' && (
                <button
                  type="button"
                  onClick={() => markGhosted.mutate(application.id)}
                  className="shrink-0 min-h-[44px] px-3 text-xs text-[var(--color-text-muted)] hover:text-amber-300"
                >
                  Mark ghosted
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {stats && (
        <div className="space-y-3">
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
                <p className="text-xs text-[var(--color-text-muted)]">
                  {label}
                </p>
              </div>
            ))}
          </div>
          <div className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3">
            <p className="text-xs uppercase tracking-wide text-[var(--color-text-muted)] mb-2">
              This week
            </p>
            <p className="text-sm text-[var(--color-text)]">
              {stats.weekly.triaged} triaged · {stats.weekly.queued} queued ·{' '}
              {stats.weekly.sent} sent · {stats.weekly.replies} replies
            </p>
            <p className="text-xs text-[var(--color-text-muted)] mt-1">
              Response rate {Math.round(stats.funnel.responseRate * 100)}%
              {stats.funnel.averageResponseDays !== null &&
                ` · ${stats.funnel.averageResponseDays} days on average`}
            </p>
          </div>
          {stats.skills.length > 0 && (
            <div className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3">
              <p className="text-xs uppercase tracking-wide text-[var(--color-text-muted)] mb-2">
                Skills in recent postings
              </p>
              <div className="flex flex-wrap gap-1.5">
                {stats.skills.map(skill => (
                  <span
                    key={skill.term}
                    className="text-xs px-2 py-1 rounded border border-white/10"
                  >
                    {skill.term} · {skill.postings}/{skill.ofPostings}
                  </span>
                ))}
              </div>
            </div>
          )}
          {stats.sources.length > 1 && (
            <div className="grid gap-2 sm:grid-cols-2">
              {stats.sources.map(source => (
                <div
                  key={source.source}
                  className="p-2 rounded border border-white/10 text-xs"
                >
                  <span className="capitalize">{source.source}</span> ·{' '}
                  {source.sent} sent · {Math.round(source.responseRate * 100)}%
                  response
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <a
        href="/api/jobs/report.html"
        download
        className="inline-flex min-h-[44px] items-center px-3 rounded border border-white/20 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
      >
        Download offline HTML report
      </a>

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
  const { data: proposals } = useQuery({
    queryKey: ['jobs', 'status-proposals'],
    queryFn: api.jobs.linkage.statusProposals,
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
  const applyProposal = useMutation({
    mutationFn: (items: { applicationId: string; emailId: string }[]) =>
      api.jobs.linkage.applyStatusProposals(items),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
  });

  if (!unlinked?.length && !proposals?.length) {
    return (
      <p className="text-sm text-[var(--color-text-muted)]">
        Every job email has found its application.
      </p>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto min-w-0 space-y-2">
      {proposals && proposals.length > 0 && (
        <div className="rounded-lg border border-[var(--color-primary)]/30 p-3 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm font-medium">
              Proposed status updates ({proposals.length})
            </p>
            <button
              type="button"
              onClick={() =>
                applyProposal.mutate(
                  proposals.map(p => ({
                    applicationId: p.applicationId,
                    emailId: p.emailId,
                  }))
                )
              }
              className="min-h-[36px] px-3 rounded text-xs border border-white/20"
            >
              Apply all
            </button>
          </div>
          {proposals.map(proposal => (
            <div
              key={proposal.emailId}
              className="rounded border border-white/10 p-2"
            >
              <p className="text-sm">
                {proposal.title} · {proposal.company}
              </p>
              <p className="text-xs text-[var(--color-text-muted)]">
                {proposal.currentStatus} → {proposal.proposedStatus} ·{' '}
                {proposal.source.subject || '(no subject)'} ·{' '}
                {proposal.source.senderEmail}
              </p>
              <button
                type="button"
                onClick={() =>
                  applyProposal.mutate([
                    {
                      applicationId: proposal.applicationId,
                      emailId: proposal.emailId,
                    },
                  ])
                }
                className="min-h-[36px] px-2 mt-1 rounded text-xs border border-white/20"
              >
                Confirm update
              </button>
            </div>
          ))}
        </div>
      )}
      {Boolean(unlinked?.length) && (
        <>
          <p className="text-xs text-[var(--color-text-muted)]">
            These job emails matched nothing confidently. Linking identifies the
            application; any status change is proposed above for confirmation.
          </p>
          {(unlinked ?? []).map(email => (
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
        </>
      )}
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
    ['upskill', 'Upskill'],
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
            {tab === 'upskill' && <Upskill />}
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
