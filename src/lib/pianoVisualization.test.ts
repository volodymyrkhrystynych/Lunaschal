import { describe, expect, it } from 'vitest';
import type { PracticeStep } from './piano';
import {
  buildFallingNotes,
  midiNoteName,
  pianoKeyGeometry,
} from './pianoVisualization';

const STEPS: PracticeStep[] = [
  { measure: 1, beat: 1, right: [60, 64], left: [48], durationBeats: 1 },
  { measure: 1, beat: 2, right: [67], left: [43], durationBeats: 2 },
  { measure: 1, beat: 4, right: [69], left: [], durationBeats: 1 },
];

describe('piano visualization', () => {
  it('aligns the full 88-note range to the 52 white-key keyboard', () => {
    expect(pianoKeyGeometry(21)).toMatchObject({
      leftPercent: 0,
      black: false,
    });
    const last = pianoKeyGeometry(108);
    expect(last?.leftPercent).toBeCloseTo((51 / 52) * 100);
    expect(last?.widthPercent).toBeCloseTo(100 / 52);
    expect(pianoKeyGeometry(22)?.black).toBe(true);
    expect(pianoKeyGeometry(20)).toBeNull();
  });

  it('places selected-hand notes at cumulative beat offsets', () => {
    expect(buildFallingNotes(STEPS, 1, 'both')).toEqual([
      {
        id: '1:right:67:0',
        note: 67,
        hand: 'right',
        beatOffset: 0,
        durationBeats: 2,
        current: true,
      },
      {
        id: '1:left:43:0',
        note: 43,
        hand: 'left',
        beatOffset: 0,
        durationBeats: 2,
        current: true,
      },
      {
        id: '2:right:69:0',
        note: 69,
        hand: 'right',
        beatOffset: 2,
        durationBeats: 1,
        current: false,
      },
    ]);
    expect(buildFallingNotes(STEPS, 0, 'left').map(item => item.note)).toEqual([
      48, 43,
    ]);
  });

  it('moves notes continuously against the metronome timeline', () => {
    expect(
      buildFallingNotes(STEPS, 0, 'right', 8, -4).map(item => item.beatOffset)
    ).toEqual([4, 4, 5, 7]);
    expect(
      buildFallingNotes(STEPS, 1, 'right', 8, 1.5).map(item => item.beatOffset)
    ).toEqual([-0.5, 1.5]);
  });

  it('formats MIDI pitches for readable note labels', () => {
    expect(midiNoteName(60)).toBe('C4');
    expect(midiNoteName(70)).toBe('A♯4');
  });
});
