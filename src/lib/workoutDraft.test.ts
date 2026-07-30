import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  clearWorkoutDraft,
  EMPTY_DRAFT,
  isDraftEmpty,
  loadWorkoutDraft,
  parseDraft,
  saveWorkoutDraft,
  type WorkoutDraft,
} from './workoutDraft';

const draft = (over: Partial<WorkoutDraft> = {}): WorkoutDraft => ({
  ...EMPTY_DRAFT,
  ...over,
});

// The node environment has no localStorage; a minimal in-memory stand-in is
// enough, and lets the quota-exceeded path be exercised.
function installStorage(overrides: Partial<Storage> = {}) {
  const store = new Map<string, string>();
  const storage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    ...overrides,
  } as Storage;
  vi.stubGlobal('localStorage', storage);
  return store;
}

beforeEach(() => {
  vi.unstubAllGlobals();
  installStorage();
});

describe('isDraftEmpty', () => {
  it('treats whitespace-only fields as empty', () => {
    expect(isDraftEmpty(EMPTY_DRAFT)).toBe(true);
    expect(isDraftEmpty(draft({ rawText: '   \n ' }))).toBe(true);
  });

  it('counts any real content, including a lone location pick', () => {
    expect(isDraftEmpty(draft({ rawText: 'squats 60,8' }))).toBe(false);
    expect(isDraftEmpty(draft({ locationType: 'outside' }))).toBe(false);
    expect(isDraftEmpty(draft({ durationMinutes: '45' }))).toBe(false);
    expect(isDraftEmpty(draft({ notes: 'felt strong' }))).toBe(false);
  });
});

describe('round trip', () => {
  it('restores a draft written before a reload', () => {
    const mid = draft({
      rawText: 'bicep curls 20,10',
      locationType: 'goodlife_brother',
      durationMinutes: '45',
      intensityRating: '8',
    });
    saveWorkoutDraft(mid);
    expect(loadWorkoutDraft()).toEqual(mid);
  });

  it('saving an empty draft clears the stored one', () => {
    saveWorkoutDraft(draft({ rawText: 'squats 60,8' }));
    saveWorkoutDraft(EMPTY_DRAFT);
    expect(loadWorkoutDraft()).toBeNull();
  });

  it('clearWorkoutDraft removes it', () => {
    saveWorkoutDraft(draft({ rawText: 'squats 60,8' }));
    clearWorkoutDraft();
    expect(loadWorkoutDraft()).toBeNull();
  });
});

describe('parseDraft', () => {
  it('returns null for missing, malformed, or empty payloads', () => {
    expect(parseDraft(null)).toBeNull();
    expect(parseDraft('not json')).toBeNull();
    expect(parseDraft('"a string"')).toBeNull();
    expect(parseDraft(JSON.stringify(EMPTY_DRAFT))).toBeNull();
  });

  it('salvages the usable fields of a partial or wrongly-typed payload', () => {
    // An older build's shape must not throw away the text the user typed.
    expect(
      parseDraft(
        JSON.stringify({
          rawText: 'squats 60,8',
          durationMinutes: 45,
          extra: true,
        })
      )
    ).toEqual(draft({ rawText: 'squats 60,8' }));
  });
});

describe('storage failures', () => {
  it('never lets a full quota break logging a workout', () => {
    installStorage({
      setItem: () => {
        throw new Error('QuotaExceededError');
      },
    });
    expect(() =>
      saveWorkoutDraft(draft({ rawText: 'squats 60,8' }))
    ).not.toThrow();
  });

  it('treats an unreadable store as no draft', () => {
    installStorage({
      getItem: () => {
        throw new Error('SecurityError');
      },
    });
    expect(loadWorkoutDraft()).toBeNull();
  });
});
