import type {
  JournalEntry,
  Transcription,
  DatedConversation,
  JournalPaper,
  FoodJournalItem,
  TaskEvent,
} from '../hooks/api';

// A journal feed item: a real entry, an interleaved STT transcription, a saved
// chat day, an archived paper (drawings), or a food-log entry. `entryIndex`
// preserves the entry's position in the original entries array so keyboard
// navigation (selIndex) keeps indexing entries only — the other kinds are not
// selectable.
export type FeedItem =
  | { kind: 'entry'; entry: JournalEntry; entryIndex: number }
  | { kind: 'transcription'; transcription: Transcription }
  | { kind: 'conversation'; conversation: DatedConversation }
  | { kind: 'paper'; paper: JournalPaper }
  | { kind: 'food'; food: FoodJournalItem }
  | { kind: 'taskEvent'; taskEvent: TaskEvent };

// Merge entries, transcriptions, saved chats, archived papers, food-log entries,
// and task events into one feed sorted by time descending (conversations sort by
// updatedAt, papers by the moment they were archived, food and task events by
// createdAt). All inputs are already newest-first; entries win exact ties.
export function buildFeed(
  entries: JournalEntry[],
  transcriptions: Transcription[],
  conversations: DatedConversation[] = [],
  papers: JournalPaper[] = [],
  food: FoodJournalItem[] = [],
  taskEvents: TaskEvent[] = []
): FeedItem[] {
  const feed: FeedItem[] = [];
  let e = 0;
  let t = 0;
  let c = 0;
  let p = 0;
  let f = 0;
  let k = 0;
  // `undefined` because JournalEntry.createdAt is optional — an optimistically
  // inserted row exists before the server has stamped one. Such a row sorts to
  // the end rather than crashing the merge or jumping to the top on NaN.
  const ms = (v: string | undefined) => (v ? new Date(v).getTime() : -Infinity);
  while (
    e < entries.length ||
    t < transcriptions.length ||
    c < conversations.length ||
    p < papers.length ||
    f < food.length ||
    k < taskEvents.length
  ) {
    const entry = entries[e];
    const tr = transcriptions[t];
    const conv = conversations[c];
    const paper = papers[p];
    const foodItem = food[f];
    const taskEvent = taskEvents[k];
    const entryTime = entry ? ms(entry.createdAt) : -Infinity;
    const trTime = tr ? ms(tr.createdAt) : -Infinity;
    const convTime = conv ? ms(conv.updatedAt) : -Infinity;
    const paperTime = paper ? ms(paper.archivedAt) : -Infinity;
    const foodTime = foodItem ? ms(foodItem.createdAt) : -Infinity;
    const taskTime = taskEvent ? ms(taskEvent.createdAt) : -Infinity;

    // Entries win ties over the other sources.
    if (
      entry &&
      entryTime >= trTime &&
      entryTime >= convTime &&
      entryTime >= paperTime &&
      entryTime >= foodTime &&
      entryTime >= taskTime
    ) {
      feed.push({ kind: 'entry', entry, entryIndex: e });
      e++;
    } else if (
      tr &&
      trTime >= convTime &&
      trTime >= paperTime &&
      trTime >= foodTime &&
      trTime >= taskTime
    ) {
      feed.push({ kind: 'transcription', transcription: tr });
      t++;
    } else if (
      conv &&
      convTime >= paperTime &&
      convTime >= foodTime &&
      convTime >= taskTime
    ) {
      feed.push({ kind: 'conversation', conversation: conv });
      c++;
    } else if (paper && paperTime >= foodTime && paperTime >= taskTime) {
      feed.push({ kind: 'paper', paper });
      p++;
    } else if (foodItem && foodTime >= taskTime) {
      feed.push({ kind: 'food', food: foodItem });
      f++;
    } else {
      feed.push({ kind: 'taskEvent', taskEvent });
      k++;
    }
  }
  return feed;
}
