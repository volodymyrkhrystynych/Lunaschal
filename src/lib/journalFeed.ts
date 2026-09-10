import type {
  JournalEntry,
  Transcription,
  DatedConversation,
  JournalPaper,
  FoodJournalItem,
  TaskEvent,
  JournalNewspaper,
  JournalStudySource,
} from '../hooks/api';

// A journal feed item: a real entry, an interleaved STT transcription, a saved
// chat day, an archived paper (drawings), a food-log entry, a task event, an
// archived newspaper issue, or a filed study source. `entryIndex` preserves the
// entry's position in the original entries array so keyboard navigation
// (selIndex) keeps indexing entries only — the other kinds are not selectable.
export type FeedItem =
  | { kind: 'entry'; entry: JournalEntry; entryIndex: number }
  | { kind: 'transcription'; transcription: Transcription }
  | { kind: 'conversation'; conversation: DatedConversation }
  | { kind: 'paper'; paper: JournalPaper }
  | { kind: 'food'; food: FoodJournalItem }
  | { kind: 'taskEvent'; taskEvent: TaskEvent }
  | { kind: 'newspaper'; newspaper: JournalNewspaper }
  | { kind: 'study'; study: JournalStudySource };

// `undefined` because JournalEntry.createdAt is optional — an optimistically
// inserted row exists before the server has stamped one. Such a row sorts to
// the end rather than crashing the merge or jumping to the top on NaN.
const ms = (v: string | undefined) => (v ? new Date(v).getTime() : -Infinity);

// One source feeding the merge: its remaining items, how to read a timestamp
// off one, and how to turn one into a feed item.
type Source<T> = {
  items: T[];
  at: (item: T) => number;
  make: (item: T, index: number) => FeedItem;
};

function source<T>(
  items: T[],
  at: (item: T) => number,
  make: (item: T, index: number) => FeedItem
): Source<unknown> {
  return { items, at, make } as Source<unknown>;
}

// Merge entries, transcriptions, saved chats, archived papers, food-log
// entries, task events, archived newspaper issues and filed study sources into
// one feed sorted by time descending (conversations sort by updatedAt,
// newspapers by the moment the issue was archived, papers and study sources by
// the moment they were last worked on inside the day they were filed under —
// see backend/journal_moment.py — the rest by createdAt). All inputs are
// already newest-first.
//
// An n-way merge over a list of sources rather than a cascade of comparisons:
// with seven sources the cascade needed every arm rewritten to add one, and an
// arm that forgot a comparand would silently mis-order the feed. The eighth
// below cost one entry, which is what this shape is for. Ties go to the
// earliest source in this list, which is why entries are first — an entry still
// wins an exact timestamp tie against every other kind.
export function buildFeed(
  entries: JournalEntry[],
  transcriptions: Transcription[],
  conversations: DatedConversation[] = [],
  papers: JournalPaper[] = [],
  food: FoodJournalItem[] = [],
  taskEvents: TaskEvent[] = [],
  newspapers: JournalNewspaper[] = [],
  studySources: JournalStudySource[] = []
): FeedItem[] {
  const sources = [
    source(
      entries,
      e => ms(e.createdAt),
      (entry, entryIndex) => ({ kind: 'entry', entry, entryIndex })
    ),
    source(
      transcriptions,
      t => ms(t.createdAt),
      transcription => ({ kind: 'transcription', transcription })
    ),
    source(
      conversations,
      c => ms(c.updatedAt),
      conversation => ({ kind: 'conversation', conversation })
    ),
    source(
      papers,
      p => ms(p.archivedAt),
      paper => ({ kind: 'paper', paper })
    ),
    source(
      food,
      f => ms(f.createdAt),
      foodItem => ({ kind: 'food', food: foodItem })
    ),
    source(
      taskEvents,
      k => ms(k.createdAt),
      taskEvent => ({ kind: 'taskEvent', taskEvent })
    ),
    source(
      newspapers,
      n => ms(n.archivedAt),
      newspaper => ({ kind: 'newspaper', newspaper })
    ),
    source(
      studySources,
      s => ms(s.archivedAt),
      study => ({ kind: 'study', study })
    ),
  ];
  const cursors = sources.map(() => 0);
  const feed: FeedItem[] = [];
  const total = sources.reduce((n, s) => n + s.items.length, 0);
  for (let n = 0; n < total; n++) {
    let pick = -1;
    let best = -Infinity;
    for (let i = 0; i < sources.length; i++) {
      const item = sources[i].items[cursors[i]];
      if (item === undefined) continue;
      const at = sources[i].at(item);
      // Strictly greater: the first source to offer a timestamp keeps it.
      if (pick === -1 || at > best) {
        pick = i;
        best = at;
      }
    }
    feed.push(
      sources[pick].make(sources[pick].items[cursors[pick]], cursors[pick])
    );
    cursors[pick]++;
  }
  return feed;
}
