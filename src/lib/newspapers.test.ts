import { describe, it, expect } from 'vitest';
import {
  shiftDateISO,
  isFutureDate,
  paperDay,
  downloadWindowOpen,
  hasMissingEditions,
  issueLoadStatus,
} from './newspapers';

describe('paperDay', () => {
  it('uses the local calendar date, not the UTC one', () => {
    // 11pm local time can already be tomorrow in UTC for timezones behind UTC.
    const localLateEvening = new Date(2026, 6, 9, 23, 0, 0); // Jul 9, 11pm, local time
    expect(paperDay(localLateEvening)).toBe('2026-07-09');
  });

  it('keeps the previous day’s paper until 4am', () => {
    expect(paperDay(new Date(2026, 6, 10, 0, 30))).toBe('2026-07-09');
    expect(paperDay(new Date(2026, 6, 10, 3, 59))).toBe('2026-07-09');
    expect(paperDay(new Date(2026, 6, 10, 4, 0))).toBe('2026-07-10');
  });
});

describe('downloadWindowOpen', () => {
  it('is closed from midnight to 4am', () => {
    expect(downloadWindowOpen(new Date(2026, 6, 10, 0, 0))).toBe(false);
    expect(downloadWindowOpen(new Date(2026, 6, 10, 3, 59))).toBe(false);
    expect(downloadWindowOpen(new Date(2026, 6, 10, 4, 0))).toBe(true);
    expect(downloadWindowOpen(new Date(2026, 6, 10, 23, 59))).toBe(true);
  });
});

describe('shiftDateISO', () => {
  it('moves forward and backward within a month', () => {
    expect(shiftDateISO('2026-07-10', 1)).toBe('2026-07-11');
    expect(shiftDateISO('2026-07-10', -1)).toBe('2026-07-09');
  });

  it('crosses a month boundary', () => {
    expect(shiftDateISO('2026-07-31', 1)).toBe('2026-08-01');
    expect(shiftDateISO('2026-08-01', -1)).toBe('2026-07-31');
  });

  it('crosses a year boundary', () => {
    expect(shiftDateISO('2026-12-31', 1)).toBe('2027-01-01');
    expect(shiftDateISO('2027-01-01', -1)).toBe('2026-12-31');
  });
});

describe('isFutureDate', () => {
  const now = new Date('2026-07-10T12:00:00Z');

  it('is false for today', () => {
    expect(isFutureDate('2026-07-10', now)).toBe(false);
  });

  it('is false for yesterday', () => {
    expect(isFutureDate('2026-07-09', now)).toBe(false);
  });

  it('is true for tomorrow', () => {
    expect(isFutureDate('2026-07-11', now)).toBe(true);
  });
});

describe('hasMissingEditions', () => {
  it('is false when every paper has an image', () => {
    expect(
      hasMissingEditions([{ imageUrl: 'a.jpg' }, { imageUrl: 'b.jpg' }])
    ).toBe(false);
  });

  it('is true when any single paper is missing', () => {
    expect(
      hasMissingEditions([{ imageUrl: 'a.jpg' }, { imageUrl: null }])
    ).toBe(true);
  });

  it('is true when every paper is missing', () => {
    expect(hasMissingEditions([{ imageUrl: null }, { imageUrl: null }])).toBe(
      true
    );
  });

  it('is false for an empty list', () => {
    expect(hasMissingEditions([])).toBe(false);
  });
});

describe('issueLoadStatus', () => {
  const base = {
    loaded: 0,
    total: 0,
    pdfDone: false,
    markupDone: false,
    stalled: false,
  };

  it('reports bytes against the total while the PDF arrives', () => {
    expect(
      issueLoadStatus({ ...base, loaded: 3_200_000, total: 13_481_011 })
    ).toBe('Loading issue… 3.2 of 13.5 MB');
  });

  it('reports bytes alone when the server sent no length', () => {
    expect(issueLoadStatus({ ...base, loaded: 1_500_000 })).toBe(
      'Loading issue… 1.5 MB'
    );
  });

  it('never shows more loaded than the total', () => {
    expect(issueLoadStatus({ ...base, loaded: 14e6, total: 13e6 })).toBe(
      'Loading issue… 13.0 of 13.0 MB'
    );
  });

  it('tells a server that never answered from a transfer that stopped', () => {
    expect(issueLoadStatus({ ...base, stalled: true })).toMatch(
      /No data from the server/
    );
    expect(
      issueLoadStatus({ ...base, loaded: 2e6, total: 10e6, stalled: true })
    ).toBe('Loading stalled at 2.0 of 10.0 MB. Check the connection.');
  });

  it('names the markup fetch when that is the half still waiting', () => {
    expect(issueLoadStatus({ ...base, pdfDone: true })).toBe('Loading markup…');
    expect(issueLoadStatus({ ...base, pdfDone: true, stalled: true })).toMatch(
      /waiting for saved markup/
    );
  });
});
