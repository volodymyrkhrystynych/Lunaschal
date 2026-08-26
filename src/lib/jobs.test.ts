import { describe, expect, it } from 'vitest';
import type {
  FeedJob,
  FilledAnswer,
  JobApplication,
  JobSearch,
  MatchReasons,
  ResumeImportPreview,
  TailoredContent,
} from '@/hooks/api';
import {
  answerSummary,
  coveragePercent,
  daysUntilPurge,
  describeSearch,
  formatSalary,
  groupByStatus,
  isOpen,
  isPartialScore,
  matchBand,
  importSummary,
  matchPercent,
  parseQuestionList,
  queueBreakdown,
  rewrittenBullets,
  searchState,
  sourceNeedsSlug,
  splitFeed,
  topGaps,
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
    queuedAt: null,
    queueError: null,
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

// --------------------------------------------------------------------------
// Discovery feed
// --------------------------------------------------------------------------

function reasons(
  matched: string[],
  missing: string[],
  extra: Partial<MatchReasons> = {}
): MatchReasons {
  const total = matched.length + missing.length;
  return {
    matched,
    missing,
    coverage: total ? matched.length / total : 0,
    ...extra,
  };
}

function feedJob(id: string, matchReasons: MatchReasons | null): FeedJob {
  return {
    id,
    source: 'greenhouse',
    url: '',
    company: 'Acme',
    title: 'Engineer',
    location: 'Toronto',
    remote: false,
    salaryMin: null,
    salaryMax: null,
    salaryCurrency: '',
    description: '',
    matchScore: matchReasons?.coverage ?? null,
    dismissed: false,
    postedAt: null,
    createdAt: '2026-08-01T00:00:00Z',
    matchReasons,
    triageState: 'pending',
    triageReason: '',
    triageFit: '',
    triageSummary: '',
    triageFlags: [],
    triageAt: null,
    triageError: null,
  };
}

function search(overrides: Partial<JobSearch> = {}): JobSearch {
  return {
    id: 's1',
    kind: 'greenhouse',
    label: '',
    params: { slug: 'acme' },
    enabled: true,
    intervalHours: 24,
    lastRunAt: null,
    lastCount: null,
    lastError: null,
    ...overrides,
  };
}

describe('matchPercent', () => {
  it('reads the stored report rather than recomputing', () => {
    expect(matchPercent(reasons(['python', 'sql'], ['go', 'rust']))).toBe(50);
  });

  it('is null when the posting was never scored', () => {
    expect(matchPercent(null)).toBeNull();
  });

  it('is null when the posting mentioned no known terms at all', () => {
    // Zero of zero is not zero coverage — it means the vocabulary found
    // nothing to measure, which is a different statement.
    expect(matchPercent(reasons([], []))).toBeNull();
  });
});

describe('matchBand', () => {
  it('bands coverage coarsely', () => {
    expect(matchBand(85)).toBe('strong');
    expect(matchBand(70)).toBe('strong');
    expect(matchBand(55)).toBe('fair');
    expect(matchBand(39)).toBe('weak');
  });

  it('distinguishes unscored from badly scored', () => {
    expect(matchBand(null)).toBe('none');
    expect(matchBand(0)).toBe('weak');
  });
});

describe('topGaps', () => {
  it('keeps the order keywords.py produced', () => {
    // Ordered by how often the posting mentioned each term, so the first are
    // the ones it cares most about — re-sorting here would lose that.
    expect(topGaps(reasons([], ['kubernetes', 'terraform', 'go']), 2)).toEqual([
      'kubernetes',
      'terraform',
    ]);
  });

  it('is empty for an unscored posting', () => {
    expect(topGaps(null)).toEqual([]);
  });
});

describe('isPartialScore', () => {
  it('flags a score computed from a snippet', () => {
    expect(isPartialScore(reasons(['python'], [], { partial: true }))).toBe(
      true
    );
    expect(isPartialScore(reasons(['python'], []))).toBe(false);
    expect(isPartialScore(null)).toBe(false);
  });
});

describe('splitFeed', () => {
  it('splits without reordering either group', () => {
    const high = feedJob('high', reasons(['a', 'b', 'c'], ['d']));
    const mid = feedJob('mid', reasons(['a'], ['b']));
    const low = feedJob('low', reasons(['a'], ['b', 'c', 'd']));
    const unscored = feedJob('unscored', null);

    const { promising, rest } = splitFeed([high, mid, low, unscored]);

    expect(promising.map(j => j.id)).toEqual(['high', 'mid']);
    expect(rest.map(j => j.id)).toEqual(['low', 'unscored']);
  });

  it('treats an unscored posting as not promising rather than as zero', () => {
    const { promising, rest } = splitFeed([feedJob('x', null)]);
    expect(promising).toEqual([]);
    expect(rest).toHaveLength(1);
  });
});

describe('describeSearch', () => {
  it('prefers the label the user gave it', () => {
    expect(describeSearch(search({ label: 'Dream jobs' }))).toBe('Dream jobs');
  });

  it('falls back to the slug for a company board', () => {
    expect(describeSearch(search())).toBe('acme');
  });

  it('describes an Adzuna search by its query', () => {
    expect(
      describeSearch(
        search({ kind: 'adzuna', params: { what: 'python', where: 'Toronto' } })
      )
    ).toBe('python in Toronto');
  });

  it('handles an Adzuna search with no query at all', () => {
    expect(describeSearch(search({ kind: 'adzuna', params: {} }))).toBe(
      'any role'
    );
  });
});

describe('searchState', () => {
  it('puts an error ahead of the count', () => {
    // A search failing quietly for a week looks exactly like a search with no
    // new postings, and the feed gives no hint which it is.
    const state = searchState(
      search({
        lastRunAt: '2026-08-15T03:00:00Z',
        lastCount: 12,
        lastError: 'HTTP 500',
      })
    );
    expect(state).toEqual({ tone: 'error', text: 'HTTP 500' });
  });

  it('marks a search that has never run', () => {
    expect(searchState(search()).tone).toBe('idle');
  });

  it('reports the last count, singular and plural', () => {
    expect(
      searchState(search({ lastRunAt: '2026-08-15T03:00:00Z', lastCount: 1 }))
        .text
    ).toBe('1 posting last run');
    expect(
      searchState(search({ lastRunAt: '2026-08-15T03:00:00Z', lastCount: 4 }))
        .text
    ).toBe('4 postings last run');
  });
});

describe('sourceNeedsSlug', () => {
  it('is true for the company boards and false for Adzuna', () => {
    expect(sourceNeedsSlug('greenhouse')).toBe(true);
    expect(sourceNeedsSlug('lever')).toBe(true);
    expect(sourceNeedsSlug('ashby')).toBe(true);
    expect(sourceNeedsSlug('adzuna')).toBe(false);
  });
});

describe('queueBreakdown', () => {
  it('separates ready, still building, and failed', () => {
    const ready = {
      ...application('a', 'ready'),
      queuedAt: '2026-08-15T00:00:00Z',
    };
    const building = {
      ...application('b', 'draft'),
      queuedAt: '2026-08-15T00:00:00Z',
    };
    const failed = {
      ...application('c', 'draft'),
      queuedAt: '2026-08-15T00:00:00Z',
      queueError: 'model unavailable',
    };

    const result = queueBreakdown([ready, building, failed]);

    expect(result.ready.map(a => a.id)).toEqual(['a']);
    expect(result.building.map(a => a.id)).toEqual(['b']);
    expect(result.failed.map(a => a.id)).toEqual(['c']);
  });

  it('leaves an unqueued draft out of all three', () => {
    // Starting an application by hand is not the same as queueing it.
    const result = queueBreakdown([application('a', 'draft')]);
    expect(result).toEqual({ ready: [], building: [], failed: [] });
  });

  it('counts a failure as failed even once it is ready', () => {
    const stale = {
      ...application('a', 'ready'),
      queuedAt: '2026-08-15T00:00:00Z',
      queueError: 'timed out',
    };
    expect(queueBreakdown([stale]).failed.map(a => a.id)).toEqual(['a']);
    expect(queueBreakdown([stale]).ready).toEqual([]);
  });
});

describe('importSummary', () => {
  function preview(
    overrides: Partial<ResumeImportPreview> = {}
  ): ResumeImportPreview {
    return {
      contact: {
        fullName: '',
        email: '',
        phone: '',
        location: '',
        headline: '',
      },
      roles: [],
      skills: [],
      education: [],
      lineCount: 0,
      unusedLines: [],
      ...overrides,
    };
  }

  const role = (bullets: number) => ({
    company: 'Acme',
    title: 'Engineer',
    location: '',
    startLabel: '',
    endLabel: '',
    bullets: Array.from({ length: bullets }, (_, i) => ({
      index: i,
      text: `Bullet ${i}`,
    })),
  });

  it('counts bullets across every role', () => {
    const summary = importSummary(
      preview({ roles: [role(2), role(3)], skills: ['Python'] })
    );
    expect(summary).toMatchObject({ roles: 2, bullets: 5, skills: 1 });
  });

  it('describes what will be added', () => {
    expect(importSummary(preview({ roles: [role(1)] })).label).toBe(
      '1 role, 1 bullet'
    );
    expect(
      importSummary(preview({ roles: [role(2), role(0)], skills: ['a', 'b'] }))
        .label
    ).toBe('2 roles, 2 bullets, 2 skills');
  });

  it('says so when everything has been unticked', () => {
    // roles: 0 is what disables the commit button — an import that writes
    // nothing must not look like it worked.
    const summary = importSummary(preview());
    expect(summary.roles).toBe(0);
    expect(summary.label).toBe('nothing selected');
  });

  it('survives a preview with fields missing', () => {
    expect(
      importSummary({ roles: [{ bullets: undefined }] } as never)
    ).toMatchObject({ roles: 1, bullets: 0 });
  });
});
