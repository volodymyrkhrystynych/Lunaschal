import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import type { EmailCategory, EmailMessage } from '../../hooks/api';
import {
  EMAIL_CATEGORY_LABELS,
  JOB_STATUS_LABELS,
  formatEmailDate,
} from '../../lib/email';

export function EmailList({
  onSelect,
  selectedId,
}: {
  onSelect: (email: EmailMessage) => void;
  selectedId: string | null;
}) {
  const [category, setCategory] = useState<EmailCategory | ''>('');
  const [query, setQuery] = useState('');

  const { data: emails, isLoading } = useQuery({
    queryKey: ['email', 'list', category, query],
    queryFn: () =>
      api.email.list({
        category: category || undefined,
        query: query || undefined,
      }),
  });

  return (
    <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <button
          onClick={() => setCategory('')}
          className={`px-3 py-1 rounded-full text-xs border transition-colors ${
            category === ''
              ? 'border-[var(--color-primary)] text-[var(--color-primary)]'
              : 'border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
          }`}
        >
          All
        </button>
        {(Object.keys(EMAIL_CATEGORY_LABELS) as EmailCategory[]).map(c => (
          <button
            key={c}
            onClick={() => setCategory(c)}
            className={`px-3 py-1 rounded-full text-xs border transition-colors ${
              category === c
                ? 'border-[var(--color-primary)] text-[var(--color-primary)]'
                : 'border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
            }`}
          >
            {EMAIL_CATEGORY_LABELS[c]}
          </button>
        ))}
      </div>
      <input
        type="text"
        value={query}
        onChange={e => setQuery(e.target.value)}
        placeholder="Search…"
        className="mb-3 bg-white/5 border border-white/10 rounded px-3 py-1.5 text-sm text-[var(--color-text)] focus:outline-none focus:border-[var(--color-primary)]"
      />
      <div className="flex-1 overflow-y-auto space-y-1">
        {isLoading ? (
          <div className="text-sm text-[var(--color-text-muted)] p-4">
            Loading…
          </div>
        ) : emails?.length === 0 ? (
          <div className="text-sm text-[var(--color-text-muted)] p-4">
            No emails found.
          </div>
        ) : (
          emails?.map(email => (
            <button
              key={email.id}
              onClick={() => onSelect(email)}
              className={`w-full text-left p-3 rounded-lg border transition-colors ${
                selectedId === email.id
                  ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/10'
                  : 'border-white/10 bg-[var(--color-surface)] hover:border-white/20'
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-sm text-[var(--color-text)] truncate">
                  {email.sender || email.senderEmail || 'Unknown sender'}
                </span>
                <span className="text-xs text-[var(--color-text-muted)] shrink-0">
                  {formatEmailDate(email.receivedAt)}
                </span>
              </div>
              <p className="text-sm text-[var(--color-text)] truncate">
                {email.subject || '(no subject)'}
              </p>
              {email.snippet && (
                <p className="text-xs text-[var(--color-text-muted)] truncate">
                  {email.snippet}
                </p>
              )}
              {(email.category || email.jobStatus) && (
                <div className="flex gap-1 mt-1">
                  {email.category && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/10 text-[var(--color-text-muted)]">
                      {EMAIL_CATEGORY_LABELS[email.category]}
                    </span>
                  )}
                  {email.jobStatus && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)]">
                      {JOB_STATUS_LABELS[email.jobStatus]}
                    </span>
                  )}
                </div>
              )}
            </button>
          ))
        )}
      </div>
    </div>
  );
}
