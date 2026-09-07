import { describe, it, expect } from 'vitest';
import {
  clipButtonLabel,
  clipLabel,
  clipLengthMs,
  clipsSummary,
  formatClipLength,
} from './clipStage';

describe('clipLengthMs', () => {
  it('measures a finished clip', () => {
    expect(clipLengthMs({ startedAt: 1_000, endedAt: 43_000 })).toBe(42_000);
  });

  it('is zero while the clip is still running', () => {
    expect(clipLengthMs({ startedAt: 1_000, endedAt: null })).toBe(0);
  });

  it('never goes negative on a clock that stepped backwards', () => {
    expect(clipLengthMs({ startedAt: 5_000, endedAt: 1_000 })).toBe(0);
  });
});

describe('formatClipLength', () => {
  it('pads the seconds', () => {
    expect(formatClipLength(9_000)).toBe('0:09');
    expect(formatClipLength(69_000)).toBe('1:09');
  });

  it('lets the minutes run past an hour rather than growing a field', () => {
    // A voice memo is read as "how much of my thought is in here"; nobody is
    // looking for the hour boundary.
    expect(formatClipLength(74 * 60_000)).toBe('74:00');
  });
});

describe('clipLabel', () => {
  it('numbers from one, in record order', () => {
    const clip = { startedAt: 0, endedAt: 18_000 };
    expect(clipLabel(0, clip)).toBe('Clip 1 · 0:18');
    expect(clipLabel(1, clip)).toBe('Clip 2 · 0:18');
  });
});

describe('clipButtonLabel', () => {
  it('offers to record when idle, and to stop while recording', () => {
    expect(clipButtonLabel('idle')).toBe('● Record');
    expect(clipButtonLabel('recording')).toBe('■ Stop');
  });

  it('says Starting while the microphone is being handed over', () => {
    // getUserMedia's prompt and the first store write both land before the
    // recorder can report 'recording'. Saying nothing there is what let the
    // browser's mic indicator come on over an untouched-looking button.
    expect(clipButtonLabel('idle', true)).toBe('Starting…');
  });

  it('never says Transcribing — stopping no longer transcribes', () => {
    // The status still exists on the shared recorder (the dictation buttons
    // elsewhere use it); a staged clip merely never reaches it.
    expect(clipButtonLabel('transcribing')).toBe('Saving…');
    expect(clipButtonLabel('saving')).toBe('Saving…');
  });

  it('prefers the live states over Starting', () => {
    expect(clipButtonLabel('recording', true)).toBe('■ Stop');
  });
});

describe('clipsSummary', () => {
  it('is empty with nothing staged', () => {
    expect(clipsSummary([])).toBe('');
  });

  it('counts the clips and totals their length', () => {
    expect(
      clipsSummary([
        { startedAt: 0, endedAt: 42_000 },
        { startedAt: 0, endedAt: 18_000 },
      ])
    ).toBe('2 clips · 1:00');
  });

  it('says clip, singular, for one', () => {
    expect(clipsSummary([{ startedAt: 0, endedAt: 5_000 }])).toBe(
      '1 clip · 0:05'
    );
  });
});
