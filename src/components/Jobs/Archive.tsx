import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type ArchivedJob } from '@/hooks/api';
import { archiveReasonLabel, relativeDay } from '@/lib/jobs';

const FILTERS: [ArchivedJob['reason'] | '', string][] = [
  ['', 'All'],
  ['expired', 'Expired'],
  ['dismissed', 'Dismissed'],
  ['closed', 'Closed'],
];

/**
 * Everything in Jobs that no longer needs attention: postings left a week,
 * dismissed ones, unsent resumes, and closed applications. Newest first, and
 * the server never returns anything archived more than half a year ago.
 */
export function Archive({
  onOpen,
}: {
  onOpen: (applicationId: string) => void;
}) {
  const queryClient = useQueryClient();
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  const [reason, setReason] = useState<ArchivedJob['reason'] | ''>('');

  // Debounced so typing a company name is one request, not one per letter.
  useEffect(() => {
    const id = setTimeout(() => setQuery(input.trim()), 300);
    return () => clearTimeout(id);
  }, [input]);

  const { data: rows, isLoading } = useQuery({
    queryKey: ['jobs', 'archive', query, reason],
    queryFn: () => api.jobs.archive(query, reason),
  });

  const restore = useMutation({
    mutationFn: (jobId: string) => api.jobs.restoreArchived(jobId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
  });

  return (
    <div className="flex-1 overflow-y-auto min-w-0 space-y-3">
      <input
        type="search"
        value={input}
        onChange={e => setInput(e.target.value)}
        placeholder="Search title, company, location, description"
        aria-label="Search the archive"
        className="w-full min-h-[44px] px-3 rounded border border-white/20 bg-[var(--color-surface)] text-sm text-[var(--color-text)]"
      />
      <div className="flex gap-1 flex-wrap">
        {FILTERS.map(([key, label]) => (
          <button
            key={label}
            type="button"
            onClick={() => setReason(key)}
            aria-pressed={reason === key}
            className={`px-3 min-h-[36px] rounded text-xs border ${
              reason === key
                ? 'border-[var(--color-primary)]/40 bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                : 'border-white/10 text-[var(--color-text-muted)]'
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      <p className="text-xs text-[var(--color-text-muted)]">
        Postings you have not acted on for a week, and resumes left unsent for a
        week, move here automatically. Only the last six months are shown.
      </p>

      {restore.isError && (
        <p className="text-sm text-red-400">
          {(restore.error as Error).message}
        </p>
      )}
      {isLoading && (
        <p className="text-sm text-[var(--color-text-muted)]">Loading…</p>
      )}
      {rows && rows.length === 0 && (
        <p className="text-sm text-[var(--color-text-muted)]">
          {query ? 'Nothing archived matches that.' : 'The archive is empty.'}
        </p>
      )}

      <div className="space-y-1">
        {rows?.map(row => (
          <div
            key={row.jobId}
            className="flex items-stretch gap-2 rounded-lg border border-white/10 bg-[var(--color-surface)]"
          >
            <button
              type="button"
              disabled={!row.applicationId}
              onClick={() => row.applicationId && onOpen(row.applicationId)}
              className="flex-1 min-w-0 text-left p-3 min-h-[44px] disabled:cursor-default"
            >
              <p className="text-sm font-medium text-[var(--color-text)] truncate">
                {row.title}
              </p>
              <p className="text-xs text-[var(--color-text-muted)] truncate">
                {row.company}
                {row.location && ` · ${row.location}`}
              </p>
              <p className="text-xs text-[var(--color-text-muted)]">
                <span className="text-[var(--color-text)]">
                  {archiveReasonLabel(row)}
                </span>{' '}
                {relativeDay(row.archivedAt)}
                {row.appliedAt && ` · sent ${relativeDay(row.appliedAt)}`}
                {row.postedAt && ` · posted ${relativeDay(row.postedAt)}`}
              </p>
            </button>
            <div className="shrink-0 flex flex-col justify-center">
              {row.reason !== 'closed' && (
                <button
                  type="button"
                  onClick={() => restore.mutate(row.jobId)}
                  disabled={restore.isPending}
                  className="min-h-[44px] px-3 text-xs text-[var(--color-primary)] hover:underline"
                >
                  Restore
                </button>
              )}
              {row.url && (
                <a
                  href={row.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="flex items-center min-h-[44px] px-3 text-xs text-[var(--color-text-muted)] hover:underline"
                >
                  Posting ↗
                </a>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
