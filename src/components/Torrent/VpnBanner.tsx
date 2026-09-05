import { vpnSummary, type VpnStatus } from '@/lib/torrents';

const TONES = {
  ok: {
    box: 'border-green-500/30 bg-green-500/10',
    text: 'text-green-400',
    icon: '🔒',
  },
  warn: {
    box: 'border-yellow-500/30 bg-yellow-500/10',
    text: 'text-yellow-400',
    icon: '⚠️',
  },
  bad: {
    box: 'border-red-500/40 bg-red-500/10',
    text: 'text-red-400',
    icon: '⛔',
  },
} as const;

/**
 * The exit address, front and centre.
 *
 * This is the one question the whole feature exists to answer — *whose IP are
 * the peers seeing?* — so it is answered with the actual address rather than a
 * reassuring padlock. It is a report, not the kill switch: the kill switch is
 * that the client shares gluetun's network namespace and has no other route.
 */
export function VpnBanner({ vpn }: { vpn: VpnStatus | undefined }) {
  const { tone, headline, detail } = vpnSummary(vpn);
  const style = TONES[tone];
  return (
    <div
      className={`flex items-start gap-2 rounded border px-3 py-2 text-sm ${style.box}`}
      role={tone === 'bad' ? 'alert' : undefined}
    >
      <span aria-hidden="true">{style.icon}</span>
      <div className="min-w-0">
        <div className={`font-medium ${style.text}`}>{headline}</div>
        <div className="text-xs text-[var(--color-text-muted)] break-words">
          {detail}
        </div>
      </div>
    </div>
  );
}
