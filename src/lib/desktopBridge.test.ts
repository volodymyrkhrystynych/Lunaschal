// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { subscribeMidiEvents } from './desktopBridge';

describe('desktop MIDI events', () => {
  it('delivers a native event batch immediately and unsubscribes cleanly', () => {
    const listener = vi.fn();
    const unsubscribe = subscribeMidiEvents(listener);
    const events = [{ kind: 'noteOn' as const, note: 60, timestampMs: 123 }];

    window.dispatchEvent(new CustomEvent('lunaschal-midi', { detail: events }));
    expect(listener).toHaveBeenCalledWith(events);

    unsubscribe();
    window.dispatchEvent(new CustomEvent('lunaschal-midi', { detail: events }));
    expect(listener).toHaveBeenCalledTimes(1);
  });
});
