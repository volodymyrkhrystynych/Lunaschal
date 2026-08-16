import { describe, expect, it } from 'vitest';
import type {
  FilledAnswer,
  JobApplication,
  TailoredContent,
} from '@/hooks/api';
import {
  answerSummary,
  coveragePercent,
  daysUntilPurge,
  formatSalary,
  groupByStatus,
  isOpen,
  parseQuestionList,
  rewrittenBullets,
  STATUS_LABELS,
  PIPELINE_ORDER,
} from './jobs';

function application(
  id: string,
  status: JobApplication['status']
): JobApplication {
  return {
    id,
    jobId: `job-${id}`,
    status,
    steer: '',
    coverLetter: '',
    notes: '',
    appliedEmail: '',
    appliedAt: null,
    closedAt: null,
    purgeAfter: null,
    purgedAt: null,
    company: 'Acme',
    title: 'Backend Engineer',
    jobUrl: '',
    location: '',
  };
}

describe('groupByStatus', () => {
  it('orders groups by the pipeline, not by input order', () => {
    const groups = groupByStatus([
      application('a', 'rejected'),
      application('b', 'draft'),
      application('c', 'interview'),
    ]);
    expect(groups.map(g => g.status)).toEqual([
      'draft',
      'interview',
      'rejected',
    ]);
  });

  it('drops empty groups', () => {
    const groups = groupByStatus([application('a', 'draft')]);
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe('Draft');
  });

  it('keeps every application in exactly one group', () => {
    const items = [
      application('a', 'draft'),
      application('b', 'draft'),
      application('c', 'offer'),
    ];
    const total = groupByStatus(items).reduce((n, g) => n + g.items.length, 0);
    expect(total).toBe(3);
  });

  it('handles an empty list', () => {
    expect(groupByStatus([])).toEqual([]);
  });
});

describe('status vocabulary', () => {
  it('labels every status in the pipeline', () => {
    for (const status of PIPELINE_ORDER) {
      expect(STATUS_LABELS[status]).toBeTruthy();
    }
  });

  it('treats terminal outcomes as closed', () => {
    expect(isOpen('interview')).toBe(true);
    expect(isOpen('offer')).toBe(true);
    expect(isOpen('rejected')).toBe(false);
    expect(isOpen('withdrawn')).toBe(false);
    expect(isOpen('ghosted')).toBe(false);
  });
});

describe('daysUntilPurge', () => {
  const now = new Date('2026-08-15T12:00:00Z');

  it('counts whole days remaining', () => {
    expect(daysUntilPurge('2026-08-25T12:00:00Z', now)).toBe(10);
  });

  it('clamps a passed date to zero rather than going negative', () => {
    expect(daysUntilPurge('2026-08-01T12:00:00Z', now)).toBe(0);
  });

  it('returns null when nothing is scheduled', () => {
    expect(daysUntilPurge(null, now)).toBeNull();
    expect(daysUntilPurge('not a date', now)).toBeNull();
  });
});

describe('formatSalary', () => {
  it('formats a range in thousands', () => {
    expect(formatSalary(140000, 160000, 'CAD')).toBe('140k–160k CAD');
  });

  it('formats a single bound', () => {
    expect(formatSalary(null, 90000, 'USD')).toBe('90k USD');
    expect(formatSalary(500, null, '')).toBe('500');
  });

  it('is empty when the posting states no range', () => {
    expect(formatSalary(null, null, 'CAD')).toBe('');
  });
});

describe('tailoring summaries', () => {
  const content: TailoredContent = {
    summary: 'Backend engineer.',
    selectedBullets: [
      {
        bulletId: 'b0',
        roleId: 'r1',
        index: 0,
        company: 'Acme',
        roleTitle: 'Engineer',
        original: 'Built billing.',
        text: 'Built the billing service in Python.',
        rewritten: true,
      },
      {
        bulletId: 'b1',
        roleId: 'r1',
        index: 1,
        company: 'Acme',
        roleTitle: 'Engineer',
        original: 'Cut load time.',
        text: 'Cut load time.',
        rewritten: false,
      },
    ],
    emphasis: ['python'],
    keywords: { matched: ['python'], missing: ['kubernetes'], coverage: 0.5 },
  };

  it('reports keyword coverage as a percentage', () => {
    expect(coveragePercent(content)).toBe(50);
  });

  it('returns null when the posting named no known terms', () => {
    expect(
      coveragePercent({
        ...content,
        keywords: { matched: [], missing: [], coverage: 0 },
      })
    ).toBeNull();
    expect(coveragePercent(null)).toBeNull();
  });

  it('surfaces only the bullets the model reworded', () => {
    const changed = rewrittenBullets(content);
    expect(changed).toHaveLength(1);
    expect(changed[0].bulletId).toBe('b0');
    // The original is carried so the UI can show what changed.
    expect(changed[0].original).toBe('Built billing.');
  });
});

describe('answerSummary', () => {
  const answers: FilledAnswer[] = [
    {
      label: 'Email',
      type: 'text',
      options: [],
      answer: 'a@b.c',
      source: 'profile',
    },
    { label: 'Auth', type: 'text', options: [], answer: 'Yes', source: 'bank' },
    {
      label: 'Why us',
      type: 'textarea',
      options: [],
      answer: '…',
      source: 'generated',
    },
    {
      label: 'Odd',
      type: 'text',
      options: [],
      answer: '',
      source: 'unanswered',
    },
  ];

  it('separates what was free from what needed the model', () => {
    const summary = answerSummary(answers);
    expect(summary.free).toBe(2);
    expect(summary.generated).toBe(1);
    expect(summary.unanswered).toBe(1);
    expect(summary.ready).toBe(3);
    expect(summary.total).toBe(4);
  });

  it('handles an empty kit', () => {
    expect(answerSummary([])).toMatchObject({ total: 0, free: 0, ready: 0 });
  });
});

describe('parseQuestionList', () => {
  it('takes one question per line', () => {
    expect(
      parseQuestionList('Why do you want to work here?\nYears of Python?')
    ).toEqual([
      { label: 'Why do you want to work here?' },
      { label: 'Years of Python?' },
    ]);
  });

  it('strips list markers and numbering but keeps the question mark', () => {
    expect(parseQuestionList('- Why us?\n2. Salary?\n• Start date?')).toEqual([
      { label: 'Why us?' },
      { label: 'Salary?' },
      { label: 'Start date?' },
    ]);
  });

  it('ignores blank and single-character lines', () => {
    expect(parseQuestionList('Why us?\n\n  \n-\nSalary?')).toEqual([
      { label: 'Why us?' },
      { label: 'Salary?' },
    ]);
  });
});
