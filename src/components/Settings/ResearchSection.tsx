import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';

/**
 * The nightly repo-context job: what the app knows about its own codebase.
 * Hour defaults to 3 — the chat-title sweep owns 02:00-03:00 and the briefing
 * owns 05:00-07:00, so a 03:00-05:00 window contends with neither.
 */
export function ResearchSection() {
  const queryClient = useQueryClient();
  const [hourInput, setHourInput] = useState('3');
  const [searchTimeout, setSearchTimeout] = useState('120');
  const [deepTimeout, setDeepTimeout] = useState('600');

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });
  const { data: snapshot } = useQuery({
    queryKey: ['ideas', 'repo-context'],
    queryFn: api.ideas.repoContext,
  });

  useEffect(() => {
    if (!settings) return;
    setHourInput(String(settings.repoContextHour ?? 3));
    setSearchTimeout(String(settings.researchSearchTimeoutSeconds ?? 120));
    setDeepTimeout(String(settings.researchDeepTimeoutSeconds ?? 600));
  }, [settings]);

  const invalidateSettings = () =>
    queryClient.invalidateQueries({ queryKey: ['settings'] });

  const toggleEnabled = useMutation({
    mutationFn: (enabled: boolean) =>
      api.settings.updateAI({ repoContextEnabled: enabled }),
    onSuccess: invalidateSettings,
  });

  const saveHour = useMutation({
    mutationFn: (hour: number) =>
      api.settings.updateAI({ repoContextHour: hour }),
    onSuccess: invalidateSettings,
  });

  const saveResearch = useMutation({
    mutationFn: (data: {
      researchEnabled?: boolean;
      researchSearchProvider?: string;
      researchSearchKey?: string;
      researchSearxngUrl?: string;
      researchTimeoutEnabled?: boolean;
      researchSearchTimeoutSeconds?: number;
      researchDeepTimeoutSeconds?: number;
    }) => api.settings.updateAI(data),
    onSuccess: invalidateSettings,
  });

  // The backend clamps to 30..7200 too; doing it here as well is what keeps
  // the box from snapping back to a number the user never typed.
  const commitTimeout = (
    raw: string,
    current: number,
    key: 'researchSearchTimeoutSeconds' | 'researchDeepTimeoutSeconds',
    setInput: (v: string) => void
  ) => {
    const seconds = Number(raw);
    if (!Number.isInteger(seconds) || seconds < 30 || seconds > 7200) {
      setInput(String(current));
      return;
    }
    if (seconds !== current) saveResearch.mutate({ [key]: seconds });
  };

  const refresh = useMutation({
    mutationFn: api.ideas.refreshRepoContext,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['ideas', 'repo-context'] }),
  });

  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-sm font-medium text-[var(--color-text)]">
          Repo context
        </h3>
        <p className="text-xs text-[var(--color-text-muted)] mt-1">
          A nightly scan of this codebase — routes, tables, views, settings — so
          the Ideas agent can tell what is already built. The scan itself is
          exact, not AI-generated; the model only summarizes what changed.
        </p>
      </div>

      <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
        <input
          type="checkbox"
          checked={!!settings?.repoContextEnabled}
          onChange={e => toggleEnabled.mutate(e.target.checked)}
        />
        Scan nightly
      </label>

      <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
        Hour
        <input
          type="number"
          min={0}
          max={23}
          value={hourInput}
          onChange={e => setHourInput(e.target.value)}
          onBlur={() => {
            const hour = Number(hourInput);
            if (Number.isInteger(hour) && hour >= 0 && hour <= 23)
              saveHour.mutate(hour);
            else setHourInput(String(settings?.repoContextHour ?? 3));
          }}
          className="w-16 rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1 text-sm focus:outline-none focus:border-[var(--color-primary)]"
        />
        <span className="text-xs text-[var(--color-text-muted)]">
          runs in a two-hour window from this hour
        </span>
      </label>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
          className="px-2 py-1 rounded text-sm bg-white/10 text-[var(--color-text)] hover:bg-white/15 disabled:opacity-50"
        >
          {refresh.isPending ? 'Scanning…' : 'Scan now'}
        </button>
        {snapshot ? (
          <span className="text-xs text-[var(--color-text-muted)]">
            {snapshot.routeCount} routes · {snapshot.tableCount} tables ·{' '}
            {snapshot.gitSha?.slice(0, 7) ?? 'no git'} ·{' '}
            {new Date(snapshot.generatedAt).toLocaleString()}
          </span>
        ) : (
          <span className="text-xs text-[var(--color-text-muted)]">
            Never scanned.
          </span>
        )}
      </div>

      {snapshot?.warnings?.map(warning => (
        <p key={warning} className="text-xs text-amber-400">
          {warning}
        </p>
      ))}

      <div className="border-t border-white/10 pt-3 space-y-2">
        <div>
          <h3 className="text-sm font-medium text-[var(--color-text)]">
            Research agent
          </h3>
          <p className="text-xs text-[var(--color-text-muted)] mt-1">
            Between your own requests, the agent assesses whether each idea is
            already built and researches the ones that aren't, keeping notes in
            its wiki. It parks whenever you're waiting on the model.
          </p>
        </div>

        <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
          <input
            type="checkbox"
            checked={!!settings?.researchEnabled}
            onChange={e =>
              saveResearch.mutate({ researchEnabled: e.target.checked })
            }
          />
          Research in the background
        </label>

        {/* One provider for the whole app: the research agent and the chat
            delegate both search through it. The Web Search tab that used to
            configure its own is gone. */}
        <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
          Web search
          <select
            value={settings?.researchSearchProvider ?? ''}
            onChange={e =>
              saveResearch.mutate({ researchSearchProvider: e.target.value })
            }
            className="rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1 text-sm focus:outline-none focus:border-[var(--color-primary)]"
          >
            <option value="">None</option>
            <option value="brave">Brave</option>
            <option value="searxng">SearXNG (self-hosted)</option>
          </select>
        </label>

        {settings?.researchSearchProvider === 'brave' && (
          <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
            API key
            <input
              type="password"
              defaultValue=""
              placeholder={
                settings?.hasResearchSearchKey
                  ? '•••••• (saved)'
                  : 'Paste the key'
              }
              onBlur={e => {
                if (e.target.value.trim())
                  saveResearch.mutate({
                    researchSearchKey: e.target.value.trim(),
                  });
              }}
              className="flex-1 rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1 text-sm focus:outline-none focus:border-[var(--color-primary)]"
            />
          </label>
        )}

        {settings?.researchSearchProvider === 'searxng' && (
          <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
            SearXNG URL
            <input
              defaultValue={settings?.researchSearxngUrl ?? ''}
              placeholder="http://localhost:8888"
              onBlur={e =>
                saveResearch.mutate({
                  researchSearxngUrl: e.target.value.trim(),
                })
              }
              className="flex-1 rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1 text-sm focus:outline-none focus:border-[var(--color-primary)]"
            />
          </label>
        )}

        {settings?.researchEnabled && !settings?.researchSearchProvider && (
          <p className="text-xs text-amber-400">
            With no search provider the agent can still judge what's already
            built, but it won't research anything new.
          </p>
        )}
      </div>

      {/* Everything bounding a research run before this was a count — turns,
          fetches, tokens — so a run that took an hour was inside every limit
          it had. */}
      <div className="border-t border-white/10 pt-3 space-y-2">
        <div>
          <h3 className="text-sm font-medium text-[var(--color-text)]">
            Research time limits
          </h3>
          <p className="text-xs text-[var(--color-text-muted)] mt-1">
            When a run hits its limit it stops searching and hands what it found
            to a second pass, which writes up the best answer that material
            supports and says which parts it couldn't confirm. Writing that
            answer is allowed to run past the limit — cutting it off would throw
            away the work the limit was protecting.
          </p>
        </div>

        <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
          <input
            type="checkbox"
            checked={!!settings?.researchTimeoutEnabled}
            onChange={e =>
              saveResearch.mutate({ researchTimeoutEnabled: e.target.checked })
            }
          />
          Time-box research
        </label>

        {settings?.researchTimeoutEnabled && (
          <>
            <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
              Web search
              <input
                type="number"
                min={30}
                max={7200}
                value={searchTimeout}
                onChange={e => setSearchTimeout(e.target.value)}
                onBlur={() =>
                  commitTimeout(
                    searchTimeout,
                    settings?.researchSearchTimeoutSeconds ?? 120,
                    'researchSearchTimeoutSeconds',
                    setSearchTimeout
                  )
                }
                className="w-20 rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1 text-sm focus:outline-none focus:border-[var(--color-primary)]"
              />
              <span className="text-xs text-[var(--color-text-muted)]">
                seconds — you are watching this one
              </span>
            </label>

            <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
              Deep research
              <input
                type="number"
                min={30}
                max={7200}
                value={deepTimeout}
                onChange={e => setDeepTimeout(e.target.value)}
                onBlur={() =>
                  commitTimeout(
                    deepTimeout,
                    settings?.researchDeepTimeoutSeconds ?? 600,
                    'researchDeepTimeoutSeconds',
                    setDeepTimeout
                  )
                }
                className="w-20 rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1 text-sm focus:outline-none focus:border-[var(--color-primary)]"
              />
              <span className="text-xs text-[var(--color-text-muted)]">
                seconds — never longer than the reply limit
              </span>
            </label>
          </>
        )}
      </div>

      {snapshot?.changeSummary && (
        <details className="text-xs text-[var(--color-text-muted)]">
          <summary className="cursor-pointer">What changed recently</summary>
          <p className="mt-1 whitespace-pre-wrap">{snapshot.changeSummary}</p>
        </details>
      )}
    </section>
  );
}
