import type { EmailAccountStatus } from '../../hooks/api';
import { EMAIL_PROVIDER_LABELS, openEmailSignIn } from '../../lib/email';

/** A provider refused the stored sign-in; only the user can fix that. */
export function ReconnectBanner({
  accounts,
}: {
  accounts: EmailAccountStatus[];
}) {
  return accounts
    .filter(a => a.needsReconnect)
    .map(a => (
      <div
        key={a.provider}
        role="alert"
        className="mb-3 flex items-center justify-between gap-3 rounded-lg border border-red-500/50 bg-red-500/10 p-3 text-sm text-[var(--color-text)]"
      >
        <span>
          {EMAIL_PROVIDER_LABELS[a.provider]} is disconnected. {a.emailAddress}{' '}
          needs you to sign in again
          {a.provider === 'imap' ? ' in Settings → Email' : ''}.
        </span>
        {a.provider !== 'imap' && (
          <button
            onClick={() => openEmailSignIn(a.provider)}
            className="shrink-0 px-3 py-1.5 rounded border border-white/20 bg-white/5 hover:bg-white/10"
          >
            Reconnect
          </button>
        )}
      </div>
    ));
}
