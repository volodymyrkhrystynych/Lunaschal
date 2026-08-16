import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import { formatSalary, groupByStatus, STATUS_LABELS } from '@/lib/jobs';
import { useMasterDetail } from '@/hooks/useMasterDetail';
import { ApplicationDetail } from './ApplicationDetail';
import { ProfileEditor } from './ProfileEditor';

type Tab = 'pipeline' | 'postings' | 'profile' | 'inbox';

function AddJob({ onCreated }: { onCreated: (jobId: string) => void }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState('');
  const [text, setText] = useState('');
  const queryClient = useQueryClient();

  const create = useMutation({
    mutationFn: () => api.jobs.create(url.trim() ? { url } : { text }),
    onSuccess: job => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
      setUrl('');
      setText('');
      setOpen(false);
      onCreated(job.id);
    },
  });

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="min-h-[44px] px-4 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10"
      >
        Add a posting
      </button>
    );
  }

  return (
    <div className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3 space-y-2">
      <input
        value={url}
        onChange={e => setUrl(e.target.value)}
        placeholder="Paste the posting URL…"
        className="w-full min-h-[44px] p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm text-[var(--color-text)]"
      />
      <p className="text-xs text-[var(--color-text-muted)]">
        or paste the posting text (for pages that need a login)
      </p>
      <textarea
        value={text}
        onChange={e => setText(e.target.value)}
        rows={4}
        className="w-full p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm text-[var(--color-text)] resize-y"
      />
      {create.isError && (
        <p className="text-sm text-red-400">
          {(create.error as Error).message}
        </p>
      )}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => create.mutate()}
          disabled={create.isPending || (!url.trim() && !text.trim())}
          className="min-h-[44px] px-4 rounded text-sm bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40 disabled:opacity-50"
        >
          {create.isPending ? 'Reading…' : 'Add'}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="min-h-[44px] px-4 rounded text-sm border border-white/20 bg-white/5"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

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

  return (
    <div className="flex-1 overflow-y-auto min-w-0 space-y-4">
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

function Postings({ onOpen }: { onOpen: (applicationId: string) => void }) {
  const queryClient = useQueryClient();
  const { data: jobs } = useQuery({
    queryKey: ['jobs', 'list'],
    queryFn: () => api.jobs.list(),
  });

  const apply = useMutation({
    mutationFn: (jobId: string) => api.jobs.applications.create(jobId),
    onSuccess: result => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
      onOpen(result.id);
    },
  });

  return (
    <div className="flex-1 overflow-y-auto min-w-0 space-y-3">
      <AddJob onCreated={jobId => apply.mutate(jobId)} />

      {(jobs ?? []).map(job => (
        <div
          key={job.id}
          className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3"
        >
          <p className="text-sm font-medium text-[var(--color-text)]">
            {job.title}
          </p>
          <p className="text-xs text-[var(--color-text-muted)]">
            {job.company}
            {job.location && ` · ${job.location}`}
            {formatSalary(job.salaryMin, job.salaryMax, job.salaryCurrency) &&
              ` · ${formatSalary(job.salaryMin, job.salaryMax, job.salaryCurrency)}`}
          </p>
          <div className="mt-2 flex items-center gap-2">
            {job.applicationId ? (
              <button
                type="button"
                onClick={() => onOpen(job.applicationId as string)}
                className="min-h-[36px] px-3 rounded text-xs border border-white/20 bg-white/5 hover:bg-white/10"
              >
                Open · {STATUS_LABELS[job.applicationStatus ?? 'draft']}
              </button>
            ) : (
              <button
                type="button"
                onClick={() => apply.mutate(job.id)}
                className="min-h-[36px] px-3 rounded text-xs bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40"
              >
                Start an application
              </button>
            )}
          </div>
        </div>
      ))}
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
  const [tab, setTab] = useState<Tab>('pipeline');
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
    ['pipeline', 'Pipeline'],
    ['postings', 'Postings'],
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
            {tab === 'pipeline' && <Pipeline onOpen={open} />}
            {tab === 'postings' && <Postings onOpen={open} />}
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
