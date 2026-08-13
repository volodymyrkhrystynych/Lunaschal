import { describe, it, expect } from 'vitest';
import type { StoredRecording } from '../offline/recordingStore';
import {
  PENDING_GRACE_MS,
  durationLabel,
  pendingRecordingLabel,
  visibleRecordings,
} from './recordings';

const NOW = new Date('2026-08-12T14:32:00').getTime();

function rec(over: Partial<StoredRecording> = {}): StoredRecording {
  return {
    id: 'r1',
    mode: 'audio',
    mimeType: 'audio/mp4',
    startedAt: NOW - 125_000,
    endedAt: NOW - 1_000,
    chunkCount: 25,
    finalized: true,
    recovered: false,
    attempts: 0,
    lastError: null,
    failed: false,
    ...over,
  };
}

describe('visibleRecordings', () => {
  it('says nothing about a recording that is uploading normally', () => {
    // The happy path is a couple of hundred milliseconds. Narrating it would
    // put a warning on screen every time the feature works.
    expect(visibleRecordings([rec()], NOW)).toEqual([]);
  });

  it('surfaces one that has been waiting a while', () => {
    const stuck = rec({ endedAt: NOW - PENDING_GRACE_MS - 1 });
    expect(visibleRecordings([stuck], NOW)).toHaveLength(1);
  });

  it('surfaces one whose upload has failed, immediately', () => {
    const failed = rec({ attempts: 1, lastError: 'HTTP 500' });
    expect(visibleRecordings([failed], NOW)).toHaveLength(1);
  });

  it('surfaces a terminal failure, which needs the user', () => {
    expect(visibleRecordings([rec({ failed: true })], NOW)).toHaveLength(1);
  });

  it('says nothing about a recording still in progress', () => {
    const live = rec({ finalized: false, endedAt: null });
    expect(visibleRecordings([live], NOW)).toEqual([]);
  });
});

describe('durationLabel', () => {
  it('is mm:ss', () => {
    expect(durationLabel(rec())).toBe('2:04');
    expect(durationLabel(rec({ endedAt: rec().startedAt + 7_000 }))).toBe(
      '0:07'
    );
  });

  it('is absent until the recording ends', () => {
    expect(durationLabel(rec({ endedAt: null }))).toBeNull();
  });
});

describe('pendingRecordingLabel', () => {
  it('leads with when it was made and how long it is', () => {
    expect(pendingRecordingLabel(rec(), NOW)).toMatch(/^14:29 · 2:04/);
  });

  it('distinguishes a refusal from a retry', () => {
    expect(
      pendingRecordingLabel(rec({ failed: true, lastError: 'too large' }), NOW)
    ).toContain('too large');
    expect(
      pendingRecordingLabel(rec({ attempts: 2, lastError: 'HTTP 500' }), NOW)
    ).toContain('retrying — HTTP 500');
  });

  it('says a recording that ended early was still saved', () => {
    expect(pendingRecordingLabel(rec({ recovered: true }), NOW)).toContain(
      'ended early, saved'
    );
  });

  it('distinguishes "waiting to upload" from "waiting for the server"', () => {
    expect(pendingRecordingLabel(rec(), NOW)).toContain('waiting to upload');
    expect(
      pendingRecordingLabel(rec({ endedAt: NOW - 120_000 }), NOW)
    ).toContain('waiting for the server');
  });
});
