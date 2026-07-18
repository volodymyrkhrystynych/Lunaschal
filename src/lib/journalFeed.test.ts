import { describe, it, expect } from 'vitest';
import { buildFeed } from './journalFeed';
import type { JournalEntry, Transcription } from '../hooks/api';

function entry(id: string, createdAt: string): JournalEntry {
  return {
    id,
    createdAt,
    content: `entry ${id}`,
    rawContent: null,
    title: null,
    tags: null,
    curatedTags: [],
    updatedAt: createdAt,
  };
}

function transcription(id: string, createdAt: string): Transcription {
  return {
    id,
    createdAt,
    text: `transcription ${id}`,
    source: 'paste',
    app: null,
    detail: null,
  };
}

describe('buildFeed', () => {
  it('returns an empty feed for empty inputs', () => {
    expect(buildFeed([], [])).toEqual([]);
  });

  it('passes through entries only, preserving order and index', () => {
    const entries = [
      entry('a', '2026-07-08T12:00:00'),
      entry('b', '2026-07-07T12:00:00'),
    ];
    const feed = buildFeed(entries, []);
    expect(feed).toEqual([
      { kind: 'entry', entry: entries[0], entryIndex: 0 },
      { kind: 'entry', entry: entries[1], entryIndex: 1 },
    ]);
  });

  it('passes through transcriptions only', () => {
    const ts = [
      transcription('t1', '2026-07-08T12:00:00'),
      transcription('t2', '2026-07-07T12:00:00'),
    ];
    const feed = buildFeed([], ts);
    expect(feed.map(i => i.kind)).toEqual(['transcription', 'transcription']);
  });

  it('interleaves by createdAt descending', () => {
    const entries = [
      entry('e1', '2026-07-08T12:00:00'),
      entry('e2', '2026-07-06T12:00:00'),
    ];
    const ts = [
      transcription('t1', '2026-07-07T12:00:00'),
      transcription('t2', '2026-07-05T12:00:00'),
    ];
    const feed = buildFeed(entries, ts);
    expect(
      feed.map(i => (i.kind === 'entry' ? i.entry.id : i.transcription.id))
    ).toEqual(['e1', 't1', 'e2', 't2']);
  });

  it('preserves original entryIndex when transcriptions are interleaved', () => {
    const entries = [
      entry('e1', '2026-07-08T12:00:00'),
      entry('e2', '2026-07-06T12:00:00'),
    ];
    const ts = [transcription('t1', '2026-07-07T12:00:00')];
    const feed = buildFeed(entries, ts);
    const entryItems = feed.filter(i => i.kind === 'entry');
    expect(entryItems).toEqual([
      { kind: 'entry', entry: entries[0], entryIndex: 0 },
      { kind: 'entry', entry: entries[1], entryIndex: 1 },
    ]);
  });

  it('puts the entry first on an exact timestamp tie', () => {
    const entries = [entry('e1', '2026-07-08T12:00:00')];
    const ts = [transcription('t1', '2026-07-08T12:00:00')];
    const feed = buildFeed(entries, ts);
    expect(feed.map(i => i.kind)).toEqual(['entry', 'transcription']);
  });
});
