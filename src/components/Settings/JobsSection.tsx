import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';

/**
 * Adzuna credentials and the tailored-resume retention policy.
 *
 * Adzuna is the only job source that needs a key — Greenhouse, Lever and Ashby
 * boards are public — so the feed works without anything here. The keys follow
 * the Google OAuth pattern above: written but never read back, with the
 * settings payload carrying only whether they are set.
 */
export function JobsSection() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });

  const [appId, setAppId] = useState('');
  const [appKey, setAppKey] = useState('');

  const updateAI = useMutation({
    mutationFn: api.settings.updateAI,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const configured = settings?.hasAdzunaCredentials ?? false;

  return (
    <>
      <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10 space-y-3">
        <p className="text-sm text-[var(--color-text-muted)]">
          Adzuna aggregates broadly and has a free API tier. Register at{' '}
          <code>developer.adzuna.com</code> for an app ID and key. The company
          boards in the Jobs → Feed → Sources panel need no credentials.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="text-sm text-[var(--color-text-muted)]">
              Adzuna app ID
            </label>
            <input
              type="text"
              value={appId}
              onChange={e => setAppId(e.target.value)}
              placeholder={configured ? '••••••••' : '12ab34cd'}
              className="mt-1 w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
            />
          </div>
          <div>
            <label className="text-sm text-[var(--color-text-muted)]">
              Adzuna app key
            </label>
            <input
              type="password"
              value={appKey}
              onChange={e => setAppKey(e.target.value)}
              placeholder={configured ? '••••••••••••••••' : ''}
              className="mt-1 w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
            />
          </div>
        </div>

        <button
          onClick={() => {
            updateAI.mutate({ adzunaAppId: appId, adzunaAppKey: appKey });
            setAppId('');
            setAppKey('');
          }}
          disabled={!appId.trim() || !appKey.trim()}
          className="px-3 py-1.5 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10 text-[var(--color-text)] disabled:opacity-50"
        >
          {configured ? 'Replace credentials' : 'Save credentials'}
        </button>

        <div className="pt-3 border-t border-white/10 space-y-3">
          <label className="flex items-start gap-2 text-sm text-[var(--color-text)]">
            <input
              type="checkbox"
              checked={settings?.jobTriageEnabled ?? true}
              onChange={e =>
                updateAI.mutate({ jobTriageEnabled: e.target.checked })
              }
              className="mt-1"
            />
            <span>
              Read and filter new postings
              <span className="block text-xs text-[var(--color-text-muted)]">
                A board lists every opening it has, so the feed is a whole job
                board unless something filters it. With this on, obvious
                non-software titles are dropped for free and the rest are read
                once and condensed to a couple of sentences. Off leaves every
                posting in the feed, unsummarised — nothing is deleted either
                way, and what was filtered stays reviewable in the feed.
              </span>
            </span>
          </label>
        </div>

        <div className="pt-3 border-t border-white/10 space-y-3">
          <p className="text-sm text-[var(--color-text-muted)]">
            Tailored resumes are deleted on whichever clock runs out first. The
            structured version is kept forever either way — only the rendered
            PDF and DOCX are removed.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <NumberField
              label="Delete resumes after (days)"
              value={settings?.jobRetentionDays ?? 180}
              onCommit={days => updateAI.mutate({ jobRetentionDays: days })}
            />
            <NumberField
              label="Grace period after a rejection (days)"
              value={settings?.jobRejectionGraceDays ?? 30}
              onCommit={days =>
                updateAI.mutate({ jobRejectionGraceDays: days })
              }
            />
          </div>
          <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
            <input
              type="checkbox"
              checked={settings?.jobPurgeOnRejection ?? true}
              onChange={e =>
                updateAI.mutate({ jobPurgeOnRejection: e.target.checked })
              }
            />
            Delete sooner when a rejection arrives
          </label>
        </div>
      </div>
    </>
  );
}

function NumberField({
  label,
  value,
  onCommit,
}: {
  label: string;
  value: number;
  onCommit: (value: number) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);

  return (
    <div>
      <label className="text-sm text-[var(--color-text-muted)]">{label}</label>
      <input
        type="number"
        min={1}
        value={draft ?? String(value)}
        onChange={e => setDraft(e.target.value)}
        onBlur={() => {
          // Commit on blur rather than per keystroke: typing "180" would
          // otherwise save 1, then 18, then 180.
          const parsed = Math.max(1, parseInt(draft ?? '', 10) || value);
          setDraft(null);
          if (parsed !== value) onCommit(parsed);
        }}
        className="mt-1 w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
      />
    </div>
  );
}
