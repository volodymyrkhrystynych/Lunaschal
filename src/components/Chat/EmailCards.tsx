import { EMAIL_REASON_LABELS, parseEmailNotice } from '../../lib/email';

/** The emails a message is about; the Email tab is where they are read. */
export function EmailCards({ metadata }: { metadata?: string | null }) {
  const notice = parseEmailNotice(metadata);
  if (!notice) return null;
  if (notice.kind === 'disconnected') {
    return (
      <p className="mt-2 text-xs text-[var(--color-text-muted)]">
        Reconnect {notice.address} in the Email tab.
      </p>
    );
  }
  return (
    <ul className="mt-2 space-y-1">
      {notice.items.map(item => (
        <li
          key={item.id}
          className="rounded-lg border border-white/10 bg-[var(--color-surface)] px-3 py-2 text-sm"
        >
          <span className="font-medium text-[var(--color-text)]">
            {item.sender}
          </span>
          {item.reason && (
            <span className="ml-2 text-[10px] text-[var(--color-primary)]">
              {EMAIL_REASON_LABELS[item.reason]}
            </span>
          )}
          <div className="text-xs text-[var(--color-text-muted)]">
            {item.subject}
          </div>
        </li>
      ))}
    </ul>
  );
}
