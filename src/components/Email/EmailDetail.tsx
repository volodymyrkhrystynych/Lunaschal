import type { EmailMessage } from '../../hooks/api';
import { EmailBody } from './EmailBody';
import {
  EMAIL_CATEGORY_LABELS,
  JOB_STATUS_LABELS,
  formatEmailDate,
} from '../../lib/email';

export function EmailDetail({
  email,
  onClose,
}: {
  email: EmailMessage;
  onClose: () => void;
}) {
  return (
    <div className="w-full max-w-lg shrink-0 flex flex-col overflow-hidden rounded-lg border border-white/10 bg-[var(--color-surface)]">
      <div className="flex items-start justify-between p-4 border-b border-white/10">
        <div className="min-w-0">
          <h2 className="text-lg font-medium text-[var(--color-text)] truncate">
            {email.subject || '(no subject)'}
          </h2>
          <p className="text-sm text-[var(--color-text-muted)] truncate">
            {email.sender
              ? `${email.sender} <${email.senderEmail}>`
              : email.senderEmail}
          </p>
          <p className="text-xs text-[var(--color-text-muted)]">
            {formatEmailDate(email.receivedAt)}
          </p>
        </div>
        <button
          onClick={onClose}
          className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] text-xl leading-none shrink-0 ml-2"
        >
          ✕
        </button>
      </div>
      {(email.category || email.jobStatus) && (
        <div className="flex gap-2 px-4 pt-3">
          {email.category && (
            <span className="text-xs px-2 py-0.5 rounded bg-white/10 text-[var(--color-text-muted)]">
              {EMAIL_CATEGORY_LABELS[email.category]}
            </span>
          )}
          {email.jobStatus && (
            <span className="text-xs px-2 py-0.5 rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)]">
              {JOB_STATUS_LABELS[email.jobStatus]}
            </span>
          )}
        </div>
      )}
      <div className="flex-1 overflow-y-auto p-4">
        <EmailBody html={email.bodyHtml || ''} text={email.bodyText} />
      </div>
    </div>
  );
}
