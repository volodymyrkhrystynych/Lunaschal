import { describe, it, expect } from 'vitest';
import { buildFeed, type FeedItem } from './journalFeed';
import type {
  JournalEntry,
  Transcription,
  DatedConversation,
  JournalPaper,
} from '../hooks/api';

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

function conversation(id: string, updatedAt: string): DatedConversation {
  return {
    id,
    title: `chat ${id}`,
    dayKey: updatedAt.slice(0, 10),
    messageCount: 4,
    createdAt: updatedAt,
    updatedAt,
  };
}

function paper(id: string, archivedAt: string): JournalPaper {
  return {
    id,
    title: `paper ${id}`,
    journalDate: archivedAt.slice(0, 10),
    archivedAt,
    pages: [{ id: `${id}-p0`, imageUrl: `/img/${id}` }],
  };
}

function idOf(i: FeedItem): string {
  if (i.kind === 'entry') return i.entry.id;
  if (i.kind === 'transcription') return i.transcription.id;
  if (i.kind === 'paper') return i.paper.id;
  return i.conversation.id;
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
    expect(feed.map(idOf)).toEqual(['e1', 't1', 'e2', 't2']);
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

  it('interleaves conversations by updatedAt among all three sources', () => {
    const entries = [
      entry('e1', '2026-07-08T12:00:00'),
      entry('e2', '2026-07-05T12:00:00'),
    ];
    const ts = [transcription('t1', '2026-07-06T12:00:00')];
    const convs = [
      conversation('c1', '2026-07-07T12:00:00'),
      conversation('c2', '2026-07-04T12:00:00'),
    ];
    const feed = buildFeed(entries, ts, convs);
    expect(feed.map(idOf)).toEqual(['e1', 'c1', 't1', 'e2', 'c2']);
  });

  it('keeps conversation items non-selectable (no entryIndex)', () => {
    const convs = [conversation('c1', '2026-07-07T12:00:00')];
    const feed = buildFeed([], [], convs);
    expect(feed).toEqual([{ kind: 'conversation', conversation: convs[0] }]);
    expect('entryIndex' in feed[0]).toBe(false);
  });

  it('preserves entryIndex when conversations interleave', () => {
    const entries = [
      entry('e1', '2026-07-08T12:00:00'),
      entry('e2', '2026-07-06T12:00:00'),
    ];
    const convs = [conversation('c1', '2026-07-07T12:00:00')];
    const feed = buildFeed(entries, [], convs);
    expect(feed.filter(i => i.kind === 'entry')).toEqual([
      { kind: 'entry', entry: entries[0], entryIndex: 0 },
      { kind: 'entry', entry: entries[1], entryIndex: 1 },
    ]);
  });

  it('lets entries win a tie against a conversation', () => {
    const entries = [entry('e1', '2026-07-08T12:00:00')];
    const convs = [conversation('c1', '2026-07-08T12:00:00')];
    const feed = buildFeed(entries, [], convs);
    expect(feed.map(i => i.kind)).toEqual(['entry', 'conversation']);
  });

  it('interleaves archived papers by archivedAt across all four sources', () => {
    const entries = [entry('e1', '2026-07-08T12:00:00')];
    const ts = [transcription('t1', '2026-07-06T12:00:00')];
    const convs = [conversation('c1', '2026-07-05T12:00:00')];
    const papers = [
      paper('p1', '2026-07-07T12:00:00'),
      paper('p2', '2026-07-04T12:00:00'),
    ];
    const feed = buildFeed(entries, ts, convs, papers);
    expect(feed.map(idOf)).toEqual(['e1', 'p1', 't1', 'c1', 'p2']);
  });

  it('keeps paper items non-selectable (no entryIndex)', () => {
    const papers = [paper('p1', '2026-07-07T12:00:00')];
    const feed = buildFeed([], [], [], papers);
    expect(feed).toEqual([{ kind: 'paper', paper: papers[0] }]);
    expect('entryIndex' in feed[0]).toBe(false);
  });
});
