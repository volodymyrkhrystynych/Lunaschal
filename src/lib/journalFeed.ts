import type { JournalEntry, Transcription } from '../hooks/api';

// A journal feed item: either a real entry or an interleaved STT transcription.
// `entryIndex` preserves the entry's position in the original entries array so
// keyboard navigation (selIndex) keeps indexing entries only — transcriptions
// are not selectable.
export type FeedItem =
  | { kind: 'entry'; entry: JournalEntry; entryIndex: number }
  | { kind: 'transcription'; transcription: Transcription };

// Merge entries and transcriptions into one feed sorted by createdAt
// descending. Both inputs are already newest-first; entries win exact ties.
export function buildFeed(
  entries: JournalEntry[],
  transcriptions: Transcription[]
): FeedItem[] {
  const feed: FeedItem[] = [];
  let e = 0;
  let t = 0;
  while (e < entries.length || t < transcriptions.length) {
    const entry = entries[e];
    const tr = transcriptions[t];
    if (
      entry &&
      (!tr ||
        new Date(entry.createdAt).getTime() >= new Date(tr.createdAt).getTime())
    ) {
      feed.push({ kind: 'entry', entry, entryIndex: e });
      e++;
    } else {
      feed.push({ kind: 'transcription', transcription: tr });
      t++;
    }
  }
  return feed;
}
