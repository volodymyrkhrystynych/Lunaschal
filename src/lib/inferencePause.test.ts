import { describe, it, expect } from 'vitest';
import {
  headline,
  pausedFor,
  queueSentence,
  type Headline,
} from './inferencePause';
import type { InferenceState } from '../hooks/api';

const NOW = 1_700_000_000_000; // ms

function state(over: Partial<InferenceState> = {}): InferenceState {
  return {
    paused: false,
    pausedSince: null,
    model: 'qwen36',
    modelStatus: 'loaded',
    queueDepth: 0,
    lanes: {},
    ...over,
  };
}

describe('pausedFor', () => {
  it('is empty when nothing is paused', () => {
    expect(pausedFor(null, NOW)).toBe('');
  });

  it('reads coarsely, from seconds up to days', () => {
    const since = NOW / 1000;
    expect(pausedFor(since, NOW)).toBe('just now');
    expect(pausedFor(since - 90, NOW)).toBe('1 min');
    expect(pausedFor(since - 45 * 60, NOW)).toBe('45 min');
    expect(pausedFor(since - 60 * 60, NOW)).toBe('1 hour');
    expect(pausedFor(since - 5 * 3600, NOW)).toBe('5 hours');
    expect(pausedFor(since - 26 * 3600, NOW)).toBe('1 day');
    expect(pausedFor(since - 3 * 86400, NOW)).toBe('3 days');
  });

  it('never reads negative when the clocks disagree', () => {
    expect(pausedFor(NOW / 1000 + 500, NOW)).toBe('just now');
  });
});

describe('queueSentence', () => {
  it('promises the work will run, rather than just counting it', () => {
    expect(queueSentence(state({ paused: true, queueDepth: 4 }))).toBe(
      "4 jobs waiting — they'll run when you turn this back on."
    );
  });

  it('says one job in the singular', () => {
    expect(queueSentence(state({ paused: true, queueDepth: 1 }))).toContain(
      '1 job waiting'
    );
  });

  it('reassures when the queue is empty and running', () => {
    expect(queueSentence(state())).toContain('up to date');
  });
});

describe('headline', () => {
  it('reports a loaded model as on', () => {
    const h: Headline = headline(state());
    expect(h.tone).toBe('ok');
    expect(h.title).toBe('GPU inference is on');
    expect(h.detail).toContain('holding VRAM');
  });

  it('says the model loads lazily when it is not resident', () => {
    expect(headline(state({ modelStatus: 'unloaded' })).detail).toContain(
      'loads on the next request'
    );
  });

  it('reports a clean pause with how long the card has been free', () => {
    const h = headline(
      state({
        paused: true,
        modelStatus: 'unloaded',
        pausedSince: Math.floor(Date.now() / 1000) - 2 * 3600,
      })
    );
    expect(h.tone).toBe('paused');
    expect(h.title).toBe('Paused for gaming');
    expect(h.detail).toContain('2 hours');
  });

  it('warns when the flag took but the VRAM was not released', () => {
    // The whole reason the user pressed the button was to free the card, so
    // "paused" while the model is still resident must not read as success.
    const h = headline(state({ paused: true, modelStatus: 'loaded' }));
    expect(h.tone).toBe('warn');
    expect(h.title).toContain('still loaded');
  });

  it('does not claim anything before the first fetch', () => {
    expect(headline(undefined).detail).toBe('Checking…');
  });
});
