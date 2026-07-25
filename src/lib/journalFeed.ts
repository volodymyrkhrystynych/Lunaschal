import type {
  JournalEntry,
  Transcription,
  DatedConversation,
} from '../hooks/api';

// A journal feed item: a real entry, an interleaved STT transcription, or a
// saved chat day. `entryIndex` preserves the entry's position in the original
// entries array so keyboard navigation (selIndex) keeps indexing entries only —
// transcriptions and conversations are not selectable.
export type FeedItem =
  | { kind: 'entry'; entry: JournalEntry; entryIndex: number }
  | { kind: 'transcription'; transcription: Transcription }
  | { kind: 'conversation'; conversation: DatedConversation };

// Merge entries, transcriptions, and saved chats into one feed sorted by
// createdAt descending (conversations sort by their last activity, updatedAt).
// All inputs are already newest-first; entries win exact ties.
export function buildFeed(
  entries: JournalEntry[],
  transcriptions: Transcription[],
  conversations: DatedConversation[] = []
): FeedItem[] {
  const feed: FeedItem[] = [];
  let e = 0;
  let t = 0;
  let c = 0;
  const ms = (v: string) => new Date(v).getTime();
  while (
    e < entries.length ||
    t < transcriptions.length ||
    c < conversations.length
  ) {
    const entry = entries[e];
    const tr = transcriptions[t];
    const conv = conversations[c];
    const entryTime = entry ? ms(entry.createdAt) : -Infinity;
    const trTime = tr ? ms(tr.createdAt) : -Infinity;
    const convTime = conv ? ms(conv.updatedAt) : -Infinity;

    // Entries win ties over both other sources.
    if (entry && entryTime >= trTime && entryTime >= convTime) {
      feed.push({ kind: 'entry', entry, entryIndex: e });
      e++;
    } else if (tr && trTime >= convTime) {
      feed.push({ kind: 'transcription', transcription: tr });
      t++;
    } else {
      feed.push({ kind: 'conversation', conversation: conv });
      c++;
    }
  }
  return feed;
}
