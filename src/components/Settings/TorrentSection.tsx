import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { vpnSummary } from '../../lib/torrents';

/**
 * How Lunaschal reaches the torrent stack, plus the defaults new torrents get.
 *
 * These are qBittorrent's WebUI credentials, not ProtonVPN's. The WireGuard
 * private key is deliberately not settable here — it lives in a gitignored
 * `torrent/.env` beside the compose file that consumes it, because this
 * database is backed up nightly and the seeder ships in a public repo.
 */
export function TorrentSection() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });
  const { data: vpn } = useQuery({
    queryKey: ['torrent-vpn'],
    queryFn: api.torrents.vpn,
    retry: false,
  });

  const [clientUrl, setClientUrl] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [vpnUrl, setVpnUrl] = useState('');
  const [requireVpn, setRequireVpn] = useState(true);
  const [retention, setRetention] = useState('0');
  const [ratio, setRatio] = useState('');

  useEffect(() => {
    if (!settings) return;
    setClientUrl(settings.torrentClientUrl ?? '');
    setUsername(settings.torrentUsername ?? '');
    setVpnUrl(settings.torrentVpnUrl ?? '');
    setRequireVpn(settings.torrentRequireVpn ?? true);
    setRetention(String(settings.torrentDefaultRetentionDays ?? 0));
    setRatio(
      settings.torrentDefaultRatioLimit != null
        ? String(settings.torrentDefaultRatioLimit)
        : ''
    );
    // The password never comes back from the server — only whether one is set.
  }, [settings]);

  const save = useMutation({
    mutationFn: () =>
      api.settings.updateAI({
        torrentClientUrl: clientUrl,
        torrentUsername: username,
        // Only send it when the field was actually typed into, or saving any
        // other field here would blank a working password.
        ...(password ? { torrentPassword: password } : {}),
        torrentVpnUrl: vpnUrl,
        torrentRequireVpn: requireVpn,
        torrentDefaultRetentionDays: Number(retention) || 0,
        torrentDefaultRatioLimit: ratio === '' ? null : Number(ratio),
      }),
    onSuccess: () => {
      setPassword('');
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      queryClient.invalidateQueries({ queryKey: ['torrent-vpn'] });
    },
  });

  const summary = vpnSummary(vpn);

  return (
    <div className="space-y-3 text-sm">
      <div className="text-xs text-[var(--color-text-muted)]">
        {summary.headline} — {summary.detail}
      </div>

      <label className="flex flex-col gap-1">
        <span className="text-xs text-[var(--color-text-muted)]">
          qBittorrent WebUI URL
        </span>
        <input
          value={clientUrl}
          onChange={e => setClientUrl(e.target.value)}
          placeholder="http://127.0.0.1:8080"
          className="rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1"
        />
      </label>

      <div className="grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1">
          <span className="text-xs text-[var(--color-text-muted)]">
            Username
          </span>
          <input
            value={username}
            onChange={e => setUsername(e.target.value)}
            className="rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-[var(--color-text-muted)]">
            Password {settings?.hasTorrentPassword ? '(set)' : ''}
          </span>
          <input
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder={settings?.hasTorrentPassword ? '••••••••' : ''}
            className="rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1"
          />
        </label>
      </div>

      <label className="flex flex-col gap-1">
        <span className="text-xs text-[var(--color-text-muted)]">
          gluetun control server
        </span>
        <input
          value={vpnUrl}
          onChange={e => setVpnUrl(e.target.value)}
          placeholder="http://127.0.0.1:8000"
          className="rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1"
        />
      </label>

      <label className="flex items-start gap-2">
        <input
          type="checkbox"
          checked={requireVpn}
          onChange={e => setRequireVpn(e.target.checked)}
          className="mt-1"
        />
        <span>
          Refuse to add a torrent while the tunnel is down
          <span className="block text-xs text-[var(--color-text-muted)]">
            The client cannot leak either way — it shares gluetun&apos;s network
            namespace and has no other route. This only stops a torrent sitting
            at 0% looking like a dead swarm.
          </span>
        </span>
      </label>

      <div className="grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1">
          <span className="text-xs text-[var(--color-text-muted)]">
            Default delete-after (days, 0 = keep)
          </span>
          <input
            type="number"
            min={0}
            value={retention}
            onChange={e => setRetention(e.target.value)}
            className="rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-[var(--color-text-muted)]">
            Default stop-seeding ratio
          </span>
          <input
            value={ratio}
            onChange={e => setRatio(e.target.value)}
            placeholder="qBittorrent global"
            className="rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1"
          />
        </label>
      </div>

      <button
        onClick={() => save.mutate()}
        disabled={save.isPending}
        className="px-3 py-1.5 rounded bg-[var(--color-primary)] text-black text-sm font-medium disabled:opacity-40"
      >
        {save.isPending ? 'Saving…' : 'Save'}
      </button>
      {save.isError && (
        <div className="text-sm text-red-400">
          {(save.error as Error).message}
        </div>
      )}
    </div>
  );
}
