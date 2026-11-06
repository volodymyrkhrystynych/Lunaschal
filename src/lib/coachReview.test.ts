import { describe, expect, it } from 'vitest';
import {
  explainPrompt,
  itemStatus,
  parseCoach,
  parseReview,
  stars,
  tally,
} from './coachReview';

const briefing = JSON.stringify({
  briefing: true,
  review: {
    day: '2026-11-02',
    rating: 3,
    computedRating: 3,
    overrideReason: null,
    done: 1,
    missed: 1,
    excused: 1,
    items: [
      {
        kind: 'habit',
        id: 'a',
        title: 'Stretch',
        done: true,
        excused: false,
        excuse: null,
      },
      {
        kind: 'habit',
        id: 'b',
        title: 'Read',
        done: false,
        excused: false,
        excuse: null,
      },
      {
        kind: 'weight',
        id: null,
        title: 'Weigh-in',
        done: false,
        excused: true,
        excuse: 'Away',
      },
      {
        kind: 'goal',
        id: 'g',
        title: 'Pushups',
        done: null,
        excused: false,
        excuse: null,
      },
    ],
  },
});

describe('coach review metadata', () => {
  it('reads the review on a briefing', () => {
    const review = parseReview(briefing)!;
    expect(stars(review.rating)).toBe('★★★☆☆');
    expect(tally(review)).toBe('1 of 2 done · 1 excused');
    expect(review.items.map(itemStatus)).toEqual([
      'done',
      'missed',
      'excused',
      'unknown',
    ]);
    expect(explainPrompt(review)).toBe('About yesterday — Read: ');
    expect(parseReview('{"briefing": true}')).toBeNull();
    expect(parseReview('not json')).toBeNull();
    expect(parseReview(null)).toBeNull();
  });

  it('reads check-in and rollover messages', () => {
    expect(
      parseCoach(
        JSON.stringify({ coach: { kind: 'checkin', day: 'd', open: [] } })
      )
    ).toEqual({ kind: 'checkin', day: 'd', open: [] });
    const rollover = parseCoach(
      JSON.stringify({
        coach: {
          kind: 'rollover',
          day: 'd',
          horizons: ['week'],
          goals: [{ title: 'Inbox', rating: 4, periodLabel: 'Week of Oct 26' }],
        },
      })
    );
    expect(rollover?.kind === 'rollover' && rollover.horizons).toEqual([
      'week',
    ]);
    expect(
      parseCoach(JSON.stringify({ coach: { kind: 'other', day: 'd' } }))
    ).toBeNull();
  });

  it('describes days with nothing to count', () => {
    const empty = {
      ...parseReview(briefing)!,
      done: 0,
      missed: 0,
      excused: 0,
      items: [],
    };
    expect(tally(empty)).toBe('Nothing tracked');
    expect(tally({ ...empty, excused: 2 })).toBe('Excused');
    expect(stars(null)).toBe('');
  });
});
