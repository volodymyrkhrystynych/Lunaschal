import { describe, expect, it } from 'vitest';
import { jazzVoicingAccepted, scorePerformance } from './pianoPractice';

const steps = [0, 1, 2].map(index => ({
  measure: 1,
  beat: index + 1,
  right: [60 + index],
  left: [],
  durationBeats: 1,
}));

describe('scorePerformance', () => {
  it('scores steady on-time notes and physical releases', () => {
    const events = steps.flatMap((_, index) => [
      {
        kind: 'noteOn' as const,
        note: 60 + index,
        timestampMs: index * 500,
        velocity: 80,
      },
      {
        kind: 'noteOff' as const,
        note: 60 + index,
        timestampMs: index * 500 + 500,
      },
    ]);
    expect(scorePerformance(steps, 'right', events, 120)).toEqual({
      onsetAccuracy: 100,
      durationAccuracy: 100,
      tempoStability: 100,
      velocityEvenness: 100,
      achievedTempo: 120,
    });
  });
  it('does not invent duration or tempo with insufficient events', () => {
    expect(
      scorePerformance(
        steps.slice(0, 1),
        'right',
        [{ kind: 'noteOn', note: 60, timestampMs: 10 }],
        60
      )
    ).toMatchObject({ durationAccuracy: null, achievedTempo: null });
  });
  it('cannot reuse one note-on for repeated expected notes', () => {
    const repeated = steps.slice(0, 2).map(step => ({ ...step, right: [60] }));
    const score = scorePerformance(
      repeated,
      'right',
      [{ kind: 'noteOn', note: 60, timestampMs: 0 }],
      120
    );
    expect(score.onsetAccuracy).toBe(50);
  });
});

describe('jazzVoicingAccepted', () => {
  it('accepts inversions and an omitted fifth, but requires guide tones', () => {
    expect(jazzVoicingAccepted(0, new Set([72, 62, 65]), 60)).toBe(true);
    expect(jazzVoicingAccepted(0, new Set([62, 69]), 60)).toBe(false);
    expect(jazzVoicingAccepted(1, new Set([71, 65, 67]), 60)).toBe(true);
  });
});
