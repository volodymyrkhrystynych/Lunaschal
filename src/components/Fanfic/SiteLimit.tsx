import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import { useState } from 'react';

export function SiteLimit() {
  const [minutes, setMinutes] = useState<string | null>(null);
  const client = useQueryClient();
  const status = useQuery({
    queryKey: ['fanfic', 'site-limit'],
    queryFn: api.fanfic.collections.limit,
    refetchInterval: 5000,
  });
  const resume = useMutation({
    mutationFn: api.fanfic.collections.resume,
    onSuccess: () => client.invalidateQueries({ queryKey: ['fanfic'] }),
  });
  const pause = useMutation({
    mutationFn: api.fanfic.collections.pause,
    onSuccess: () => client.invalidateQueries({ queryKey: ['fanfic'] }),
  });
  const interval = useMutation({
    mutationFn: api.fanfic.collections.setInterval,
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['fanfic', 'site-limit'] });
      setMinutes(null);
    },
  });
  const s = status.data;
  const browserMode = useMutation({
    mutationFn: api.fanfic.collections.setBrowserMode,
    onSuccess: () => client.invalidateQueries({ queryKey: ['fanfic'] }),
  });
  if (!s) return null;
  const cooling = s.cooldownUntil * 1000 > Date.now();
  return (
    <div
      role="status"
      className="mb-3 rounded border border-amber-500/30 p-3 text-sm"
    >
      <label>
        FF.net download method{' '}
        <select
          className="rounded border border-white/20 bg-[var(--color-bg)] p-1"
          value={s.browser?.mode ?? 'http'}
          disabled={browserMode.isPending}
          onChange={event =>
            browserMode.mutate(event.target.value as 'http' | 'browser')
          }
        >
          <option value="http">Direct HTTP (saved cookies)</option>
          <option value="browser">Browser session (Chrome extension)</option>
        </select>
      </label>
      {s.browser?.mode === 'browser' && (
        <div className="my-2">
          <p>
            {s.browser.connected
              ? 'Browser connected.'
              : 'Waiting for the browser extension to connect.'}
          </p>
          {s.browser.needsAttention && <p>{s.browser.message}</p>}
          <p>
            In the Lunaschal extension, open FF.net downloads and connect. Keep
            its control and download tabs open. Complete any challenge or
            sign-in in the FF.net tab, then choose Continue there.
          </p>
          <details>
            <summary>Browser setup</summary>
            <p>
              In Chrome / Chromium, open chrome://extensions, enable Developer
              mode, and load the extension folder from your Lunaschal checkout.
              If already installed, reload it. Set the Lunaschal server address
              in extension Settings. Connect grants access to FF.net; your
              cookies stay in the browser.
            </p>
          </details>
        </div>
      )}
      <p>
        {s.paused
          ? s.reason || 'FF.net downloads paused.'
          : cooling
            ? 'FF.net downloads cooling down.'
            : `FF.net imports and updates: one request every ${s.interval / 60} minutes.`}
      </p>
      {!s.paused && s.nextRequest * 1000 > Date.now() && (
        <p>
          Next request no earlier than{' '}
          {new Date(s.nextRequest * 1000).toLocaleString()}.
        </p>
      )}
      <p>
        Queued stories and saved chapters are retained. Other sites can
        continue.
      </p>
      <p>
        Pause takes effect before the next request; a request already in
        progress may finish.
      </p>
      <button
        className="mt-2 rounded border px-3 py-1"
        disabled={resume.isPending || pause.isPending}
        onClick={() => (s.paused ? resume.mutate() : pause.mutate())}
      >
        {s.paused ? 'Resume FF.net downloads' : 'Pause FF.net downloads'}
      </button>
      <form
        className="mt-3 flex flex-wrap items-center gap-2"
        onSubmit={event => {
          event.preventDefault();
          interval.mutate(Math.round(Number(minutes ?? s.interval / 60) * 60));
        }}
      >
        <label>
          Minutes between FF.net requests{' '}
          <input
            type="number"
            min="0.25"
            max="1440"
            step="0.25"
            required
            className="w-24 rounded border border-white/20 bg-[var(--color-bg)] p-1"
            value={minutes ?? s.interval / 60}
            disabled={interval.isPending}
            onChange={event => setMinutes(event.target.value)}
          />
        </label>
        <button
          className="rounded border px-3 py-1"
          disabled={interval.isPending}
        >
          {interval.isPending ? 'Saving…' : 'Save interval'}
        </button>
      </form>
      {resume.error && <p role="alert">{resume.error.message}</p>}
      {pause.error && <p role="alert">{pause.error.message}</p>}
      {interval.error && <p role="alert">{interval.error.message}</p>}
      {browserMode.error && <p role="alert">{browserMode.error.message}</p>}
    </div>
  );
}
