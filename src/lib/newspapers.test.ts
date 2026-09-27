import { describe, it, expect } from 'vitest';
import {
  shiftDateISO,
  isFutureDate,
  todayISO,
  hasMissingEditions,
  issueLoadStatus,
} from './newspapers';

describe('todayISO', () => {
  it('uses the local calendar date, not the UTC one', () => {
    // 11pm local time can already be tomorrow in UTC for timezones behind
    // UTC — todayISO must still report the viewer's local "today" so it
    // stays in sync with the backend's `date.today()` (also local).
    const localLateEvening = new Date(2026, 6, 9, 23, 0, 0); // Jul 9, 11pm, local time
    expect(todayISO(localLateEvening)).toBe('2026-07-09');
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
