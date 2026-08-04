import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';

type Provider = '' | 'brave' | 'tavily' | 'searxng';

export function WebSearchSection() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });

  const [provider, setProvider] = useState<Provider>('');
  const [key, setKey] = useState('');
  const [searxngUrl, setSearxngUrl] = useState('');

  useEffect(() => {
    if (settings) {
      setProvider((settings.websearchSearchProvider as Provider) || '');
      setSearxngUrl(settings.websearchSearxngUrl || '');
    }
  }, [settings]);

  const save = useMutation({
    mutationFn: (data: {
      websearchSearchProvider?: string;
      websearchSearchKey?: string;
      websearchSearxngUrl?: string;
    }) => api.settings.updateAI(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const commitProvider = (next: Provider) => {
    setProvider(next);
    save.mutate({ websearchSearchProvider: next });
  };

  const hasKey = settings?.hasWebsearchSearchKey ?? false;

  return (
    <section className="mb-8">
      <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">
        Web Search
      </h2>
      <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10 space-y-4">
        <p className="text-sm text-[var(--color-text-muted)]">
          Powers the "Web Search" tab in Chat. Without a provider configured it
          still answers, but says plainly that it couldn't search.
        </p>
        <div>
          <label className="text-sm text-[var(--color-text-muted)]">
            Provider
          </label>
          <select
            value={provider}
            onChange={e => commitProvider(e.target.value as Provider)}
            className="mt-1 block w-full max-w-xs bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
          >
            <option value="">None (disabled)</option>
            <option value="brave">Brave Search</option>
            <option value="tavily">Tavily</option>
            <option value="searxng">SearXNG (self-hosted)</option>
          </select>
        </div>

        {(provider === 'brave' || provider === 'tavily') && (
          <div>
            <label className="text-sm text-[var(--color-text-muted)]">
              API key
            </label>
            <div className="flex gap-2 mt-1">
              <input
                type="password"
                value={key}
                onChange={e => setKey(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                placeholder={hasKey ? 'key set — paste to replace' : 'API key'}
                className="flex-1 bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
              />
              <button
                onClick={() => {
                  save.mutate({ websearchSearchKey: key });
                  setKey('');
                }}
                disabled={!key.trim() || save.isPending}
                className="px-4 py-2 bg-[var(--color-primary)] text-white rounded text-sm hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
              >
                Save
              </button>
            </div>
          </div>
        )}

        {provider === 'searxng' && (
          <div>
            <label className="text-sm text-[var(--color-text-muted)]">
              SearXNG base URL
            </label>
            <input
              type="text"
              value={searxngUrl}
              onChange={e => setSearxngUrl(e.target.value)}
              onBlur={() =>
                save.mutate({ websearchSearxngUrl: searxngUrl.trim() })
              }
              placeholder="http://localhost:8888"
              className="mt-1 w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
            />
          </div>
        )}
      </div>
    </section>
  );
}
