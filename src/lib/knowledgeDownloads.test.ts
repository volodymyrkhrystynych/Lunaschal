import { describe, expect, it } from 'vitest';
import type { KnowledgeDownload } from '@/hooks/api';
import {
  actionsFor,
  etaLabel,
  isActive,
  percent,
  progressLabel,
  rateLabel,
  statusLabel,
} from './knowledgeDownloads';

const GB = 1024 * 1024 * 1024;

function download(over: Partial<KnowledgeDownload> = {}): KnowledgeDownload {
  return {
    id: 'd1',
    name: 'devdocs_en_sinon',
    filename: 'devdocs_en_sinon_2026-08.zim',
    title: 'Sinon.JS Docs',
    status: 'downloading',
    error: null,
    totalBytes: 100,
    downloadedBytes: 25,
    bytesPerSecond: null,
    sourceUrl: 'https://mirror.example.org/a.zim',
    createdAt: 0,
    finishedAt: null,
    ...over,
  };
}

describe('percent', () => {
  it('is null while the total is unknown, not zero', () => {
    // A bar pinned at 0% reads as stuck; an absent bar reads as "not yet
    // known", and those are different things to tell someone.
    expect(percent(download({ totalBytes: null }))).toBeNull();
    expect(percent(download())).toBe(25);
  });

  it('stays inside 0–100 even if the counters disagree', () => {
    expect(percent(download({ downloadedBytes: 120 }))).toBe(100);
    expect(percent(download({ downloadedBytes: -5 }))).toBe(0);
  });
});

describe('etaLabel', () => {
  it('says nothing without a measured rate', () => {
    expect(etaLabel(download({ bytesPerSecond: 0 }))).toBeNull();
    expect(etaLabel(download({ bytesPerSecond: null }))).toBeNull();
  });

  it('coarsens as the wait grows', () => {
    const big = (rate: number, remaining: number) =>
      etaLabel(
        download({
          totalBytes: remaining,
          downloadedBytes: 0,
          bytesPerSecond: rate,
        })
      );
    expect(big(10, 300)).toBe('30s left');
    expect(big(10, 6000)).toBe('10 min left');
    // A 107 GB archive on a slow line is hours, and seconds there are noise.
    expect(big(5 * 1024 * 1024, 107 * GB)).toBe('6.1 h left');
  });

  it('says nothing once there is nothing left to fetch', () => {
    expect(
      etaLabel(download({ downloadedBytes: 100, bytesPerSecond: 10 }))
    ).toBeNull();
  });
});

describe('rateLabel and progressLabel', () => {
  it('reuses the library formatter rather than a second one', () => {
    expect(rateLabel(5 * 1024 * 1024)).toBe('5.0 MB/s');
    expect(rateLabel(0)).toBeNull();
    expect(
      progressLabel(download({ downloadedBytes: 2 * GB, totalBytes: 7 * GB }))
    ).toBe('2.0 GB of 7.0 GB');
  });

  it('drops the total when there is not one', () => {
    expect(
      progressLabel(download({ downloadedBytes: 2048, totalBytes: null }))
    ).toBe('2 KB');
  });
});

describe('statusLabel', () => {
  it('spells out why a finished transfer is still busy', () => {
    // Verifying is its own phase: a resumed transfer cannot carry an
    // incremental hash, so the whole file is read back at the end.
    expect(statusLabel('verifying')).toBe('Checking the file');
    expect(statusLabel('done')).toBe('Installed');
  });
});

describe('actionsFor', () => {
  it('offers resume on a failure, not just dismissal', () => {
    // The `.part` is kept on a checksum failure precisely so the bytes are
    // not lost; hiding Resume would throw them away.
    expect(actionsFor(download({ status: 'error' }))).toEqual({
      pause: false,
      resume: true,
      remove: true,
    });
  });

  it('will not delete a download out from under a running thread', () => {
    expect(actionsFor(download({ status: 'downloading' })).remove).toBe(false);
    expect(actionsFor(download({ status: 'paused' })).remove).toBe(true);
  });
});

describe('isActive', () => {
  it('keeps failures in the strip and drops finished ones', () => {
    expect(isActive(download({ status: 'error' }))).toBe(true);
    expect(isActive(download({ status: 'done' }))).toBe(false);
  });
});
