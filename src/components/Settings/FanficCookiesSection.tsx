import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';

function FanficCookieRow({
  domain,
  hasCookie,
  updatedAt,
}: {
  domain: string;
  hasCookie: boolean;
  updatedAt: string | null;
}) {
  const [value, setValue] = useState('');
  const queryClient = useQueryClient();

  const save = useMutation({
    mutationFn: (cookie: string) => api.fanfic.cookies.put(domain, cookie),
    onSuccess: () => {
      setValue('');
      queryClient.invalidateQueries({ queryKey: ['fanfic', 'cookies'] });
    },
  });

  return (
    <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-medium text-[var(--color-text)]">{domain}</h3>
        {hasCookie ? (
          <span className="text-xs text-green-400">
            cookie set
            {updatedAt &&
              ` · ${new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(new Date(updatedAt))}`}
          </span>
        ) : (
          <span className="text-xs text-[var(--color-text-muted)]">
            no cookie
          </span>
        )}
      </div>
      <div className="flex gap-2">
        <input
          type="text"
          value={value}
          onChange={e => setValue(e.target.value)}
          spellCheck={false}
          autoComplete="off"
          placeholder={
            hasCookie
              ? 'paste a new cookie to replace the stored one'
              : 'xf_user=...; xf_session=...; cf_clearance=...'
          }
          className="flex-1 bg-transparent font-mono text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]"
        />
        <button
          onClick={() => save.mutate(value.trim())}
          disabled={!value.trim() || save.isPending}
          className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
        >
          Save
        </button>
        {hasCookie && (
          <button
            onClick={() => save.mutate('')}
            disabled={save.isPending}
            className="px-3 py-2 text-sm text-red-400 hover:text-red-300 disabled:opacity-50"
          >
            Clear
          </button>
        )}
      </div>
    </div>
  );
}

export function FanficCookiesSection() {
  const { data: cookies } = useQuery({
    queryKey: ['fanfic', 'cookies'],
    queryFn: api.fanfic.cookies.list,
  });

  return (
    <section className="mb-8">
      <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">
        Fanfic Site Cookies
      </h2>
      <p className="text-sm text-[var(--color-text-muted)] mb-4">
        Needed for login-gated fics (e.g. Questionable Questing NSFW sections).
        Log in to the site in your browser, open DevTools (<code>F12</code>) →{' '}
        <strong>Network</strong> tab, reload the page, then right-click the
        first request → <strong>Copy Value → Copy Request Headers</strong> and
        paste the whole thing below — the <code>Cookie</code> line is extracted
        automatically. The Cookies tab's <strong>Copy All</strong> JSON, a "Copy
        as cURL" command, or a bare cookie string all work too.
      </p>
      <div className="space-y-4">
        {cookies?.map(c => (
          <FanficCookieRow
            key={c.domain}
            domain={c.domain}
            hasCookie={c.hasCookie}
            updatedAt={c.updatedAt}
          />
        ))}
      </div>
    </section>
  );
}
