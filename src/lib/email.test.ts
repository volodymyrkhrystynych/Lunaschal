import { describe, it, expect } from 'vitest';
import {
  EMAIL_CATEGORY_LABELS,
  JOB_STATUS_LABELS,
  formatEmailDate,
} from './email';

describe('EMAIL_CATEGORY_LABELS', () => {
  it('has a label for every category', () => {
    expect(Object.keys(EMAIL_CATEGORY_LABELS).sort()).toEqual(
      [
        'job_application',
        'newsletter',
        'notification',
        'other',
        'personal',
      ].sort()
    );
  });
});

describe('JOB_STATUS_LABELS', () => {
  it('has a label for every job application status', () => {
    expect(Object.keys(JOB_STATUS_LABELS).sort()).toEqual(
      ['interview_next_step', 'other_update', 'rejection', 'sent'].sort()
    );
  });
});

describe('formatEmailDate', () => {
  it('formats an ISO timestamp as a short readable date', () => {
    expect(formatEmailDate('2026-07-09T14:30:00.000Z')).toMatch(/Jul 9, 2026/);
  });
});
