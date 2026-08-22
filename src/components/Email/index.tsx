import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import type { EmailMessage } from '../../hooks/api';
import { EmailList } from './EmailList';
import { EmailDetail } from './EmailDetail';
import { JobDashboard } from './JobDashboard';

export function Email() {
  const [tab, setTab] = useState<'inbox' | 'dashboard'>('inbox');
  const [selected, setSelected] = useState<EmailMessage | null>(null);
  const queryClient = useQueryClient();

  const { data: accounts } = useQuery({
    queryKey: ['email', 'accounts'],
    queryFn: api.email.accounts,
  });

  const syncNow = useMutation({
    mutationFn: () => api.email.syncNow(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['email'] });
    },
  });

  if (!accounts?.some(a => a.connected)) {
    return (
      <div className="flex-1 flex items-center justify-center p-4">
        <div className="text-center max-w-md">
          <p className="text-[var(--color-text)] mb-2">
            No email accounts connected
          </p>
          <p className="text-sm text-[var(--color-text-muted)]">
            Connect an email account (Gmail, Outlook, or IMAP) in Settings →
            Email to start syncing and classifying your inbox.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-semibold text-[var(--color-text)]">
            Email
          </h1>
          <div className="flex gap-1">
            {(['inbox', 'dashboard'] as const).map(t => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`px-4 py-1.5 rounded text-sm transition-colors ${
                  tab === t
                    ? 'bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40'
                    : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
                }`}
              >
                {t === 'inbox' ? 'Inbox' : 'Job Dashboard'}
              </button>
            ))}
          </div>
        </div>
        <button
          onClick={() => syncNow.mutate()}
          disabled={syncNow.isPending}
          className="px-3 py-1.5 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10 text-[var(--color-text)] disabled:opacity-50"
        >
          {syncNow.isPending ? 'Syncing…' : 'Sync now'}
        </button>
      </div>

      <div className="flex-1 flex gap-4 overflow-hidden">
        {tab === 'dashboard' ? (
          <JobDashboard onSelect={setSelected} />
        ) : (
          <EmailList onSelect={setSelected} selectedId={selected?.id ?? null} />
        )}
        {selected && (
          <EmailDetail email={selected} onClose={() => setSelected(null)} />
        )}
      </div>
    </div>
  );
}
