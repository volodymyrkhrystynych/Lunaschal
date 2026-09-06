import { describe, it, expect } from 'vitest';
import { voiceEditLabel } from './calendarVoice';

describe('voiceEditLabel', () => {
  it('names the fields a spoken sentence moved', () => {
    expect(voiceEditLabel(['title'])).toBe('Updated name');
    expect(voiceEditLabel(['description'])).toBe('Updated description');
    expect(voiceEditLabel(['tags'])).toBe('Updated tags');
  });

  it('reports a retimed event as one change, not two', () => {
    // endTime moves with time whenever a start is spoken without a length, so
    // "time and end time" would count one edit twice.
    expect(voiceEditLabel(['time', 'endTime'])).toBe('Updated time');
    expect(voiceEditLabel(['endTime'])).toBe('Updated time');
  });

  it('orders the fields the same way whatever order they arrive in', () => {
    expect(voiceEditLabel(['tags', 'time', 'title'])).toBe(
      'Updated name, time, tags'
    );
    expect(voiceEditLabel(['title', 'tags', 'time'])).toBe(
      'Updated name, time, tags'
    );
  });

  it('returns null when nothing changed, for the caller to phrase', () => {
    expect(voiceEditLabel([])).toBeNull();
  });

  it('ignores a field it has no word for', () => {
    expect(voiceEditLabel(['date'])).toBeNull();
  });
});
