import type {
  EmailCategory,
  EmailProvider,
  EmailReason,
  JobApplicationStatus,
} from '../hooks/api';

export const EMAIL_CATEGORY_LABELS: Record<EmailCategory, string> = {
  job_application: 'Job Application',
  newsletter: 'Newsletter',
  notification: 'Notification',
  personal: 'Personal',
  other: 'Other',
};

export const JOB_STATUS_LABELS: Record<JobApplicationStatus, string> = {
  sent: 'Applied',
  rejection: 'Rejected',
  interview_next_step: 'Next Step',
  other_update: 'Update',
};

export function formatEmailDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

export const EMAIL_PROVIDER_LABELS: Record<EmailProvider, string> = {
  gmail: 'Gmail',
  outlook: 'Outlook',
  imap: 'Email',
};

export const EMAIL_REASON_LABELS: Record<EmailReason, string> = {
  job_next_step: 'Job next step',
  personal: 'Personal',
  security: 'Security',
  deadline: 'Deadline',
};

export function openEmailSignIn(provider: EmailProvider) {
  window.open(
    `/api/email/oauth/authorize?provider=${provider}`,
    '_blank',
    'noopener'
  );
}

export interface EmailNoticeItem {
  id: string;
  sender: string;
  subject: string;
  reason: EmailReason | null;
}

export type EmailNotice =
  | { kind: 'important'; items: EmailNoticeItem[] }
  | { kind: 'disconnected'; provider: EmailProvider; address: string };

/** `metadata.email` on a chat message the server posted about mail. */
export function parseEmailNotice(
  metadata: string | null | undefined
): EmailNotice | null {
  if (!metadata) return null;
  try {
    const email = JSON.parse(metadata)?.email;
    if (email?.kind === 'disconnected') return email;
    if (email?.kind === 'important' && Array.isArray(email.items)) return email;
  } catch {
    // Not JSON: an older message.
  }
  return null;
}
