import type { EmailCategory, JobApplicationStatus } from '../hooks/api';

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
