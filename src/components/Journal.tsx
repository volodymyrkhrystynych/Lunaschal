import { useState, useEffect, useMemo, useRef, memo } from 'react';
import {
  useQuery,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from '@tanstack/react-query';
import { api } from '../hooks/api';
import { ulid } from '../lib/ulid';
import {
  useJournalCreate,
  useJournalUpdate,
} from '../offline/mutationDefaults';
import {
  attachRecordingToEntry,
  handleFinishedRecording,
} from '../offline/recordingQueue';
import { assembleBlob, deleteRecording } from '../offline/recordingStore';
import { buildFeed, type FeedItem } from '../lib/journalFeed';
import { computeEventGroupSpans } from '../lib/journalEventGroups';
import { eventTimeLabel } from '../lib/calendar';
import { localDayKey } from '../lib/dates';
import { formatDateTime, formatDay, formatDayTime } from '../lib/dateFormats';
import {
  categoryRingBoxShadow,
  parseCategoryTags,
} from '../lib/calendarCategories';
import { isBreak, parseProposedTodos } from '../lib/chatSegments';
import type { ProposedTodo } from '../hooks/api';
import { formatCompletedAt } from '../lib/todos';
import {
  defaultNameFor,
  filesFromTransfer,
  isVoiceOnlyEntry,
  recordingFilename,
  rejectedFilesMessage,
} from '../lib/journalAttachments';
import { AttachmentButtons } from './AttachmentButtons';
import { ImageLightbox, useLightbox } from './ImageLightbox';
import { JournalAttachments } from './JournalAttachments';
import { MessageMarkdown } from './MessageMarkdown';
import type {
  DatedConversation,
  JournalEntry,
  JournalPaper,
  JournalVoiceDraft,
  FoodJournalItem,
  TaskEvent,
} from '../hooks/api';
import { ratingStars, foodTitle, mapLink, parseTags } from '../lib/food';
import { useShortcutScope } from '../shortcuts/ShortcutProvider';
import { useListSelection } from '../shortcuts/useListSelection';
import { useRecorder } from '../hooks/useRecorder';

interface JournalProps {
  /** Navigate to the fanfic reader (chip on entries linked to a fic chapter). */
  onOpenFic?: (target: { ficId: string; chapterId?: string }) => void;
}

const JOURNAL_PAGE_SIZE = 50;

/**
 * A file waiting in the compose box for the entry it belongs to to exist.
 *
 * `recordingId` is set only for a clip recorded here. It is the id of the copy
 * still sitting in IndexedDB: the audio is not let go of until the upload after
 * Save confirms it landed, so a tab that dies between the two leaves the
 * recording recoverable (`resumeStoredRecordings`) rather than gone.
 */
interface StagedFile {
  file: File;
  recordingId?: string;
}

// Long enough to swallow a burst of typing, short enough that a pause feels
// like a search rather than a delay.
const SEARCH_DEBOUNCE_MS = 250;

export function Journal({ onOpenFic }: JournalProps = {}) {
  // Two pieces of state, deliberately. `searchInput` is what the field shows
  // and updates on every keystroke; `searchQuery` is what the query key uses and
  // lags it by DEBOUNCE_MS. They were one, which meant one FTS request per
  // character typed — and, offline, one paused mutation-free query per character
  // too. The field stays fully responsive either way.
  const [searchInput, setSearchInput] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCuratedTagId, setSelectedCuratedTagId] = useState<
    string | null
  >(null);
  const [showTranscriptions, setShowTranscriptions] = useState(false);
  const [copiedTranscriptionId, setCopiedTranscriptionId] = useState<
    string | null
  >(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState('');
  const [editTitle, setEditTitle] = useState('');
  const [showNewEntry, setShowNewEntry] = useState(false);
  // The compose box's own text and staged files live in NewEntryComposer, not
  // here. Keeping them on Journal meant every keystroke re-rendered the whole
  // feed — see the comment on that component.
  const [stagedUploadError, setStagedUploadError] = useState<string | null>(
    null
  );
  const [showDelete, setShowDelete] = useState(false);
  // Dictation into the entry being edited. Only one entry is editable at a
  // time, so one recorder serves the whole list. The transcript is appended
  // rather than submitted (the BrainDump/IdeaCapture pattern) — it can be
  // corrected, or a second thought added, before Save.
  // Durable: this is a journal entry being spoken, so the audio is held on the
  // device until something confirms it landed. If transcription fails it is
  // saved as an audio entry rather than dropped (see recordingQueue).
  const [recorderNotice, setRecorderNotice] = useState('');
  // The entry id is read through a ref, not closed over: `onRecording` is
  // installed once and fires minutes later, and by then `editingId` may have
  // moved on — the audio has to land on the entry that was open when Record was
  // pressed, not whichever one is open when it finishes uploading.
  const editingIdRef = useRef<string | null>(null);
  editingIdRef.current = editingId;
  const editRecorder = useRecorder(
    text => setEditContent(prev => (prev ? `${prev}\n${text}` : text)),
    undefined,
    {
      durable: true,
      // Both halves: the text comes back here and is appended to the textarea
      // so it can be corrected before Save, and the clip itself is kept as an
      // attachment on the entry rather than being thrown away once it has been
      // read. See the button's own comment.
      deliverTranscript: true,
      onNotice: setRecorderNotice,
      onRecording: rec => {
        const entryId = editingIdRef.current;
        // No entry to hang it on (the editor was closed mid-recording): fall
        // back to the standing policy, which is a recording entry of its own.
        // Never drop the audio.
        return entryId
          ? attachRecordingToEntry(queryClient, rec, entryId)
          : handleFinishedRecording(queryClient, rec);
      },
    }
  );
  const [polishingFor, setPolishingFor] = useState<string | null>(null);
  const [polishError, setPolishError] = useState<{
    id: string;
    message: string;
  } | null>(null);
  const feedScrollRef = useRef<HTMLDivElement>(null);
  const loadMoreRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  const { data: curatedTags } = useQuery({
    queryKey: ['curatedTags'],
    queryFn: api.curatedTags.list,
  });

  // Clips from the STT listener's Journal hotkey, mid multi-model
  // transcription or stuck in error — a 'done' draft has already become a
  // normal entry in the feed below and drops out of this list server-side.
  // Refetched by the journal SSE stream's invalidation (queryKey starts with
  // 'journal', so the existing ['journal'] invalidation already covers it).
  const { data: voiceDrafts } = useQuery({
    queryKey: ['journal', 'voiceDrafts'],
    queryFn: api.journal.voiceDrafts.list,
  });

  useEffect(() => {
    // Clearing the box should feel instant — there is nothing to wait for.
    if (!searchInput) {
      setSearchQuery('');
      return;
    }
    const t = setTimeout(() => setSearchQuery(searchInput), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [searchInput]);

  const isSearching = !!searchQuery;

  // Plain/tag-filtered list is infinite-scrolled a page at a time; FTS search is
  // a single capped result set.
  const {
    data: listData,
    isLoading: listLoading,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ['journal', { curatedTagId: selectedCuratedTagId }],
    queryFn: ({ pageParam }) =>
      api.journal.list({
        curatedTagId: selectedCuratedTagId ?? undefined,
        limit: JOURNAL_PAGE_SIZE,
        offset: pageParam,
      }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) =>
      lastPage.length < JOURNAL_PAGE_SIZE
        ? undefined
        : allPages.length * JOURNAL_PAGE_SIZE,
    enabled: !isSearching,
  });

  const { data: searchResults, isLoading: searchLoading } = useQuery({
    queryKey: ['journal', 'search', searchQuery],
    queryFn: () => api.journal.search(searchQuery),
    enabled: isSearching,
  });

  // Memoized: `pages.flat()` mints a new array on every render, and every
  // derivation below keys off this identity. Without it nothing downstream can
  // ever be cached, however carefully its own deps are written.
  const entries = useMemo(
    () => (isSearching ? searchResults : listData?.pages.flat()),
    [isSearching, searchResults, listData]
  );
  const isLoading = isSearching ? searchLoading : listLoading;

  // Transcriptions only interleave in the plain chronological view — FTS search
  // doesn't cover them and a tag-filtered view is a curation context.
  const transcriptionsVisible =
    showTranscriptions && !searchQuery && !selectedCuratedTagId;

  const { data: transcriptions } = useQuery({
    queryKey: ['transcriptions'],
    queryFn: () => api.transcriptions.list(),
    enabled: transcriptionsVisible,
  });

  // Past days' chats interleave in the plain chronological view (like
  // transcriptions), collapsed by default. Not in search / tag-filtered views.
  const conversationsVisible = !searchQuery && !selectedCuratedTagId;

  const { data: chatConversations } = useQuery({
    queryKey: ['chat', 'journal-conversations'],
    queryFn: () => api.chat.journalConversations(),
    enabled: conversationsVisible,
  });

  // Archived papers (drawings) interleave in the plain chronological view too.
  const papersVisible = !searchQuery && !selectedCuratedTagId;
  const { data: journalPapers } = useQuery({
    queryKey: ['paper', 'journal'],
    queryFn: () => api.paper.journal(),
    enabled: papersVisible,
  });

  // Food-log entries interleave in the plain chronological view too.
  const foodVisible = !searchQuery && !selectedCuratedTagId;
  const { data: journalFood } = useQuery({
    queryKey: ['food', 'journal'],
    queryFn: () => api.food.journal(),
    enabled: foodVisible,
  });

  // Task completions/deletions surface as small notifications in the feed.
  const taskEventsVisible = !searchQuery && !selectedCuratedTagId;
  const { data: taskEvents } = useQuery({
    queryKey: ['taskEvents'],
    queryFn: () => api.tasks.events(),
    enabled: taskEventsVisible,
  });

  // Calendar events whose time window covers the currently-loaded entries,
  // so a transcribed/tagged event can wrap the journal entries written
  // during it in a colored border (journalEventGroups.ts). Ranged off
  // whatever's actually loaded rather than a fixed window, since the feed is
  // infinite-scrolled by page count, not by date.
  const eventsVisible = !searchQuery && !selectedCuratedTagId;
  // A reduce, not `Math.min(...times)`: the feed is unbounded (infinite scroll,
  // 50 entries a page) and spreading thousands of arguments into a call is both
  // slow and, past the engine's argument limit, a RangeError. Memoized because
  // it walks every loaded entry and allocates a Date for each.
  const [eventsRangeStart, eventsRangeEnd] = useMemo(() => {
    let min = Infinity;
    let max = -Infinity;
    for (const e of entries ?? []) {
      const t = new Date(e.createdAt).getTime();
      if (t < min) min = t;
      if (t > max) max = t;
    }
    if (min === Infinity) return [null, null] as const;
    return [localDayKey(new Date(min)), localDayKey(new Date(max))] as const;
  }, [entries]);
  const { data: calendarEvents } = useQuery({
    queryKey: ['calendar', 'journal-range', eventsRangeStart, eventsRangeEnd],
    queryFn: () => api.calendar.listByRange(eventsRangeStart!, eventsRangeEnd!),
    enabled: eventsVisible && !!eventsRangeStart && !!eventsRangeEnd,
  });

  useEffect(() => {
    const es = new EventSource('/api/journal/events');
    es.onmessage = () =>
      queryClient.invalidateQueries({ queryKey: ['journal'] });
    return () => es.close();
  }, [queryClient]);

  // Infinite scroll: load the next page when the sentinel nears the viewport.
  useEffect(() => {
    if (isSearching) return;
    if (typeof IntersectionObserver === 'undefined') return;
    const sentinel = loadMoreRef.current;
    const root = feedScrollRef.current;
    if (!sentinel || !root) return;
    const io = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting && hasNextPage && !isFetchingNextPage) {
          fetchNextPage();
        }
      },
      { root, rootMargin: '400px' }
    );
    io.observe(sentinel);
    return () => io.disconnect();
  }, [isSearching, hasNextPage, isFetchingNextPage, fetchNextPage]);

  // Offline-queueable: optimistic insert + reconciling invalidation live in the
  // registered mutation defaults. The UI reset must happen on submit, NOT in
  // onSuccess — offline the mutation is paused and onSuccess never fires until
  // reconnect, which would leave the compose box open (showing a duplicate of
  // the entry the optimistic insert already added to the feed).
  const createEntry = useJournalCreate();
  const updateEntry = useJournalUpdate();

  const submitNewEntry = (content: string, staged: StagedFile[]) => {
    const id = ulid();
    createEntry.mutate(
      {
        id,
        content,
        // Tells the server photos are coming, so the title waits for their
        // captions instead of being generated from the text alone milliseconds
        // from now. Attachments can only be uploaded once the entry exists.
        pendingAttachments: staged.length || undefined,
      },
      {
        // Per-call, so the shared offline defaults' own callbacks still run.
        // Uploads wait for the create to land: offline the mutation is paused
        // and the entry has no server-side row for an attachment to hang off.
        onSuccess: () => {
          if (staged.length) uploadStagedFiles(id, staged);
        },
      }
    );
    setStagedUploadError(null);
    setShowNewEntry(false);
  };

  // Deliberately fire-and-forget with its own error surface: the entry is
  // already saved by this point, so a failed upload must not look like a failed
  // save. Sequential so the server's `position` matches the paste order.
  const uploadStagedFiles = async (entryId: string, files: StagedFile[]) => {
    try {
      for (const staged of files) {
        await api.journal.attachments.upload(
          entryId,
          staged.file,
          defaultNameFor(staged.file.name)
        );
        // Confirmed on the server. This is the only place a *staged* recording's
        // audio may be let go of — the composer holds the IndexedDB copy until
        // exactly here, so a tab that dies before Save leaves it recoverable.
        if (staged.recordingId) await deleteRecording(staged.recordingId);
      }
    } catch (e) {
      setStagedUploadError(
        `The entry was saved, but its attachment failed to upload: ${
          (e as Error).message || 'upload failed'
        }`
      );
    } finally {
      queryClient.invalidateQueries({ queryKey: ['journal'] });
    }
  };

  const submitEdit = (id: string) => {
    updateEntry.mutate({ id, content: editContent, title: editTitle });
    setEditingId(null);
  };

  const deleteEntry = useMutation({
    mutationFn: (id: string) => api.journal.delete(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['journal'] }),
  });

  const deleteTranscription = useMutation({
    mutationFn: (id: string) => api.transcriptions.delete(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['transcriptions'] }),
  });

  const copyTranscription = async (id: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // navigator.clipboard needs a secure context; fall back for webviews that deny it
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    setCopiedTranscriptionId(id);
    setTimeout(
      () => setCopiedTranscriptionId(cur => (cur === id ? null : cur)),
      1500
    );
  };

  const polishEntry = useMutation({
    mutationFn: (id: string) => api.journal.polish(id),
    onMutate: id => {
      setPolishingFor(id);
      setPolishError(null);
    },
    onSettled: () => setPolishingFor(null),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['journal'] }),
    // A polish runs on the local llama-server, which is often simply not up.
    // Without this the button just stopped saying "Polishing..." and nothing
    // else happened, which read as a broken button rather than an offline model.
    onError: (err, id) =>
      setPolishError({
        id,
        message: (err as Error).message || 'Polish failed',
      }),
  });

  const { selIndex, next, prev, isSelected, scrollSelectedIntoView } =
    useListSelection(entries?.length, 1);

  useShortcutScope(1, {
    next,
    prev,
    create: () => setShowNewEntry(true),
    drillIn: () => {
      const entry = entries?.[selIndex];
      if (!entry) return false;
      setEditingId(entry.id);
      setEditContent(entry.content);
      setEditTitle(entry.title ?? '');
      return true;
    },
    drillOut: () => {
      if (!editingId) return false;
      setEditingId(null);
      return true;
    },
  });

  // formatDateTime, not a locally-constructed formatter. This is called once
  // per row and used to build a fresh Intl.DateTimeFormat every time — the
  // single largest per-render cost of the feed on the iPhone.
  const formatDate = formatDateTime;

  // An n-way merge sort over six unbounded lists, with a `new Date` per
  // comparison. It depends on nothing the user is typing, so it has no business
  // re-running when they type.
  const feedItems = useMemo(
    () =>
      buildFeed(
        entries ?? [],
        transcriptionsVisible ? (transcriptions ?? []) : [],
        conversationsVisible ? (chatConversations ?? []) : [],
        papersVisible ? (journalPapers ?? []) : [],
        foodVisible ? (journalFood ?? []) : [],
        taskEventsVisible ? (taskEvents ?? []) : []
      ),
    [
      entries,
      transcriptionsVisible,
      transcriptions,
      conversationsVisible,
      chatConversations,
      papersVisible,
      journalPapers,
      foodVisible,
      journalFood,
      taskEventsVisible,
      taskEvents,
    ]
  );
  // Calendar events whose window covers a run of feedItems, rendered as a
  // colored border wrapping that run — see journalEventGroups.ts. This is a
  // read-only overlay computed from feedItems, never a change to it, so
  // entryIndex/keyboard selection below is untouched.
  //
  // Memoized together with the two lookups built from it: the span computation
  // is O(events x feed) with a Date parse per item and a JSON.parse of each
  // event's category tags, and the Map/Set are pure functions of its result.
  const { spanByStartIndex, indexInSpan } = useMemo(() => {
    const spans = computeEventGroupSpans(
      feedItems,
      eventsVisible ? (calendarEvents ?? []) : []
    );
    const byStart = new Map(spans.map(s => [s.startIndex, s]));
    const covered = new Set<number>();
    for (const s of spans) {
      for (let i = s.startIndex; i <= s.endIndex; i++) covered.add(i);
    }
    return { spanByStartIndex: byStart, indexInSpan: covered };
  }, [feedItems, eventsVisible, calendarEvents]);

  const renderFeedItem = (item: FeedItem) => {
    if (item.kind === 'conversation') {
      return (
        <SavedChatItem
          key={item.conversation.id}
          conversation={item.conversation}
        />
      );
    }
    if (item.kind === 'paper') {
      return <JournalPaperItem key={item.paper.id} paper={item.paper} />;
    }
    if (item.kind === 'food') {
      return <JournalFoodItem key={item.food.id} food={item.food} />;
    }
    if (item.kind === 'taskEvent') {
      return (
        <JournalTaskEventItem
          key={item.taskEvent.id}
          event={item.taskEvent}
          formatDate={formatDate}
        />
      );
    }
    if (item.kind === 'transcription') {
      const t = item.transcription;
      return (
        <div
          key={t.id}
          className="p-3 bg-[var(--color-surface)]/50 rounded-lg border border-white/5 opacity-70"
        >
          <div className="flex items-start justify-between gap-2 mb-1">
            <div className="flex items-baseline gap-2 min-w-0">
              <span className="text-sm text-[var(--color-text-muted)] shrink-0">
                {formatDate(t.createdAt)}
              </span>
              {t.app && (
                <span className="px-2 py-0.5 text-xs rounded border border-white/20 text-[var(--color-text-muted)] bg-white/5 truncate">
                  {t.app}
                  {t.detail && ` · ${t.detail}`}
                </span>
              )}
            </div>
            <div className="flex gap-2 shrink-0">
              <button
                onClick={() => copyTranscription(t.id, t.text)}
                className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              >
                {copiedTranscriptionId === t.id ? 'Copied!' : 'Copy'}
              </button>
              {showDelete && (
                <button
                  onClick={() => deleteTranscription.mutate(t.id)}
                  className="text-sm text-red-400 hover:text-red-300"
                >
                  Delete
                </button>
              )}
            </div>
          </div>
          <div className="text-sm text-[var(--color-text-muted)] italic whitespace-pre-wrap">
            {t.text}
          </div>
        </div>
      );
    }
    const { entry, entryIndex: idx } = item;
    // Parsed once. The tag row used to `JSON.parse(entry.tags)` twice per entry
    // per render — once to decide whether to show the row at all, once to fill
    // it — and this whole function ran for every entry on every keystroke.
    const aiTags = parseTags(entry.tags);
    return (
      <div
        key={entry.id}
        ref={scrollSelectedIntoView(idx)}
        className={`p-4 bg-[var(--color-surface)] rounded-lg border ${
          isSelected(idx) ? 'border-[var(--color-primary)]' : 'border-white/10'
        }`}
      >
        <div className="flex items-start justify-between mb-2">
          <span className="text-sm text-[var(--color-text-muted)]">
            {formatDate(entry.createdAt)}
          </span>
          <div className="flex gap-2">
            {entry.rawContent && (
              <button
                onClick={() => polishEntry.mutate(entry.id)}
                disabled={polishingFor === entry.id}
                className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
              >
                {polishingFor === entry.id ? 'Polishing...' : 'Polish'}
              </button>
            )}
            <button
              onClick={() => {
                setEditingId(entry.id);
                setEditContent(entry.content);
                setEditTitle(entry.title ?? '');
              }}
              className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            >
              Edit
            </button>
            {showDelete && (
              <button
                onClick={() => deleteEntry.mutate(entry.id)}
                className="text-sm text-red-400 hover:text-red-300"
              >
                Delete
              </button>
            )}
          </div>
        </div>

        {polishError?.id === entry.id && (
          <div className="mb-2 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded text-sm text-red-400">
            {polishError.message} — the entry was left unchanged.
          </div>
        )}

        {editingId === entry.id ? (
          <div>
            {/* Wrapping the fields makes the whole editor a paste/drop
                target — see JournalAttachments. */}
            <JournalAttachments
              entryId={entry.id}
              attachments={entry.attachments}
              editable
            >
              <input
                value={editTitle}
                onChange={e => setEditTitle(e.target.value)}
                placeholder="Entry title..."
                onKeyDown={e => {
                  if (e.key === 'Escape') setEditingId(null);
                }}
                className="w-full bg-transparent text-[var(--color-text)] font-medium focus:outline-none border border-white/10 rounded p-2 mb-2"
              />
              <textarea
                value={editContent}
                onChange={e => setEditContent(e.target.value)}
                rows={4}
                autoFocus
                onKeyDown={e => {
                  if (e.key === 'Escape') setEditingId(null);
                }}
                className="w-full bg-transparent text-[var(--color-text)] resize-none focus:outline-none border border-white/10 rounded p-2"
              />
            </JournalAttachments>
            {isVoiceOnlyEntry(entry) && (
              <MergeIntoPicker
                entry={entry}
                onMerged={() => setEditingId(null)}
              />
            )}
            {editRecorder.error && (
              <p className="mt-2 text-xs text-red-400">{editRecorder.error}</p>
            )}
            {!editRecorder.error && recorderNotice && (
              <p className="mt-2 text-xs text-amber-300">{recorderNotice}</p>
            )}
            <div className="flex items-center gap-2 mt-2">
              {/* `start('audio')` even though this button transcribes: the
                  stored mode is what decides whether the *server* transcribes
                  the upload too, and it must not — the text is fetched here and
                  put in the textarea above so it can be corrected before Save.
                  The clip is kept regardless; transcribing it is no longer a
                  reason to throw it away. */}
              <button
                type="button"
                onClick={() => {
                  if (editRecorder.status === 'recording') editRecorder.stop();
                  else if (editRecorder.status === 'idle')
                    void editRecorder.start('audio');
                }}
                disabled={
                  editRecorder.status === 'transcribing' ||
                  editRecorder.status === 'saving'
                }
                title={
                  editRecorder.canTranscribe
                    ? 'Record — the audio is attached to this entry and transcribed into it'
                    : 'Offline — the recording is kept and uploaded later, but there is no transcript until the server is back'
                }
                aria-label={
                  editRecorder.status === 'recording'
                    ? 'Stop recording'
                    : 'Transcribe into this entry'
                }
                className={`px-2 py-1 rounded text-sm ${
                  editRecorder.status === 'recording'
                    ? 'bg-red-500/25 text-red-300'
                    : 'bg-white/10 text-[var(--color-text)] hover:bg-white/15'
                } disabled:opacity-50`}
              >
                {editRecorder.status === 'recording'
                  ? '■ Stop'
                  : editRecorder.status === 'transcribing'
                    ? 'Transcribing…'
                    : editRecorder.status === 'saving'
                      ? 'Saving…'
                      : '● Transcribe'}
              </button>
              <button
                onClick={() => setEditingId(null)}
                className="ml-auto px-3 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              >
                Cancel
              </button>
              <button
                onClick={() => submitEdit(entry.id)}
                className="px-3 py-1 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
              >
                Save
              </button>
            </div>
          </div>
        ) : (
          <>
            {entry.title && (
              <h3 className="text-base font-bold text-[var(--color-text)] mb-2">
                {entry.title}
              </h3>
            )}
            <div className="content-text text-[var(--color-text)] whitespace-pre-wrap">
              {entry.content}
            </div>
            {entry.rawContent && (
              <details className="mt-3">
                <summary className="text-xs text-[var(--color-text-muted)] cursor-pointer select-none hover:text-[var(--color-text)] transition-colors">
                  Original transcription
                </summary>
                <div className="mt-2 px-3 py-2 bg-white/5 rounded text-sm text-[var(--color-text-muted)] whitespace-pre-wrap italic">
                  {entry.rawContent}
                </div>
              </details>
            )}
            {/* Readable outside edit mode too — playing a recording back
                shouldn't require putting the entry into an editable state. */}
            <JournalAttachments
              entryId={entry.id}
              attachments={entry.attachments}
              editable={false}
            />
          </>
        )}

        {((entry.ficRefs?.length ?? 0) > 0 ||
          entry.curatedTags?.length > 0 ||
          aiTags.length > 0) && (
          <div className="tag-row flex flex-wrap gap-1.5 mt-2">
            {entry.ficRefs?.map(ref => (
              <button
                key={`f:${ref.ficId}:${ref.chapterId ?? ''}`}
                onClick={() =>
                  onOpenFic?.({
                    ficId: ref.ficId,
                    chapterId: ref.chapterId ?? undefined,
                  })
                }
                title="Open in reader"
                className="px-2 py-0.5 text-xs rounded border border-[var(--color-accent)]/40 text-[var(--color-accent)] bg-[var(--color-accent)]/10 hover:bg-[var(--color-accent)]/20 transition-colors"
              >
                📖 {ref.ficTitle}
                {ref.chapterTitle ? ` · ${ref.chapterTitle}` : ''}
              </button>
            ))}
            {entry.curatedTags?.map((tag: string) => (
              <span
                key={`c:${tag}`}
                className="px-2 py-0.5 text-xs rounded border border-white/20 text-[var(--color-text-muted)] bg-white/5"
              >
                #{tag}
              </span>
            ))}
            {aiTags.map(tag => (
              <span
                key={tag}
                className="px-2 py-0.5 text-xs rounded border border-[var(--color-primary)]/40 text-[var(--color-primary)] bg-[var(--color-primary)]/10"
              >
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      <div className="flex items-center justify-end mb-4">
        <div className="flex gap-2">
          <button
            onClick={() => setShowDelete(!showDelete)}
            title={showDelete ? 'Hide delete buttons' : 'Show delete buttons'}
            className={`px-4 py-2 border rounded-lg transition-colors ${
              showDelete
                ? 'border-red-400/50 text-red-400 bg-red-500/10'
                : 'border-white/20 text-[var(--color-text-muted)] hover:bg-white/10'
            }`}
          >
            🗑
          </button>
          <button
            onClick={() => setShowNewEntry(!showNewEntry)}
            className="px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors"
          >
            + New Entry
          </button>
        </div>
      </div>

      <div className="mb-4">
        <input
          type="text"
          value={searchInput}
          onChange={e => {
            setSearchInput(e.target.value);
            setSelectedCuratedTagId(null);
          }}
          placeholder="Search entries..."
          className="w-full bg-[var(--color-surface)] border border-white/10 rounded-lg px-4 py-2 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
        />
        <div className="tag-row flex flex-wrap gap-1.5 mt-2">
          {curatedTags?.map(tag => (
            <button
              key={tag.id}
              onClick={() =>
                setSelectedCuratedTagId(
                  selectedCuratedTagId === tag.id ? null : tag.id
                )
              }
              className={`px-3 py-1 text-xs rounded-full border transition-colors ${
                selectedCuratedTagId === tag.id
                  ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                  : 'border-white/20 text-[var(--color-text-muted)] hover:border-white/40 hover:text-[var(--color-text)]'
              }`}
            >
              #{tag.name}
              {tag.entryCount > 0 && (
                <span className="ml-1 opacity-60">({tag.entryCount})</span>
              )}
            </button>
          ))}
          <button
            onClick={() => setShowTranscriptions(!showTranscriptions)}
            className={`px-3 py-1 text-xs rounded-full border transition-colors ${
              showTranscriptions
                ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                : 'border-white/20 text-[var(--color-text-muted)] hover:border-white/40 hover:text-[var(--color-text)]'
            }`}
          >
            Show transcriptions
          </button>
        </div>
      </div>

      {stagedUploadError && (
        <div className="mb-4 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded text-sm text-red-400 flex items-center gap-2">
          {/* The message carries its own context — this banner covers both a
              rejected paste (before saving) and a failed upload (after). */}
          <span>{stagedUploadError}</span>
          <button
            onClick={() => setStagedUploadError(null)}
            className="ml-auto text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            Dismiss
          </button>
        </div>
      )}

      {voiceDrafts && voiceDrafts.length > 0 && (
        <VoiceDraftsPanel drafts={voiceDrafts} />
      )}

      {showNewEntry && (
        <NewEntryComposer
          onSubmit={submitNewEntry}
          onCancel={() => setShowNewEntry(false)}
        />
      )}

      <div
        ref={feedScrollRef}
        className="flex-1 overflow-y-auto overflow-x-hidden px-2 space-y-4"
      >
        {isLoading && (
          <div className="text-[var(--color-text-muted)]">Loading...</div>
        )}

        {feedItems.map((item, i) => {
          // Already rendered inside its event group's wrapper below.
          if (indexInSpan.has(i) && !spanByStartIndex.has(i)) return null;

          const span = spanByStartIndex.get(i);
          if (span) {
            const categories = parseCategoryTags(span.event.categoryTags);
            return (
              <div
                key={`event-group:${span.event.id}`}
                className="rounded-lg p-1.5"
                style={{ boxShadow: categoryRingBoxShadow(categories) }}
              >
                <div className="px-1 mb-1 flex items-baseline justify-between gap-2 text-xs text-[var(--color-text-muted)]">
                  <span className="font-medium text-[var(--color-text)]">
                    {span.event.title}
                  </span>
                  {eventTimeLabel(span.event) && (
                    <span>{eventTimeLabel(span.event)}</span>
                  )}
                </div>
                {span.event.description && (
                  <div className="px-1 pb-1.5 text-sm text-[var(--color-text)] whitespace-pre-wrap">
                    {span.event.description}
                  </div>
                )}
                <div className="space-y-2">
                  {feedItems
                    .slice(span.startIndex, span.endIndex + 1)
                    .map(renderFeedItem)}
                </div>
              </div>
            );
          }

          return renderFeedItem(item);
        })}

        {entries?.length === 0 && !isLoading && (
          <div className="text-center text-[var(--color-text-muted)] py-12">
            {searchQuery
              ? 'No entries found'
              : 'No journal entries yet. Start writing!'}
          </div>
        )}

        {!isSearching && <div ref={loadMoreRef} className="h-px" aria-hidden />}
        {isFetchingNextPage && (
          <div className="text-center text-sm text-[var(--color-text-muted)] py-3">
            Loading more…
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * The new-entry compose box.
 *
 * Its own component for one reason: **the draft text must not be state on
 * `Journal`.** It was, and so every keystroke re-ran the whole `Journal` body
 * and re-rendered the entire feed — rebuilding the merged feed, recomputing
 * event-group spans (O(events × feed), with a `new Date` per item), re-parsing
 * every entry's tags, and constructing a fresh `Intl.DateTimeFormat` per row.
 * On a desktop that was invisible. On the iPhone typing was unusable.
 *
 * Nothing here is passed down from the feed, so lifting it out costs nothing
 * and the composer now re-renders alone.
 */
function NewEntryComposer({
  onSubmit,
  onCancel,
}: {
  onSubmit: (content: string, files: StagedFile[]) => void;
  onCancel: () => void;
}) {
  const [content, setContent] = useState('');
  // Files picked, pasted, dropped or recorded here, held until the entry they
  // belong to exists server-side.
  const [files, setFiles] = useState<StagedFile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [recorderNotice, setRecorderNotice] = useState('');

  // The composer's Transcribe button. Same two halves as the one in edit mode:
  // the text is appended to the draft below, and the clip is kept.
  //
  // It can only be *staged*, not uploaded, because the entry it belongs to does
  // not exist yet — which is also why the transcript has to come back to the
  // browser rather than being written server-side. The stored recording stays
  // in IndexedDB until the upload after Save confirms it landed; see
  // `uploadStagedFiles`.
  const recorder = useRecorder(
    text => setContent(prev => (prev ? `${prev}\n${text}` : text)),
    undefined,
    {
      durable: true,
      deliverTranscript: true,
      onNotice: setRecorderNotice,
      onRecording: async rec => {
        const blob = await assembleBlob(rec.id);
        if (!blob || blob.size === 0) return;
        const name = recordingFilename(rec.mimeType);
        setError(null);
        setFiles(current => [
          ...current,
          {
            file: new File([blob], name, { type: rec.mimeType }),
            recordingId: rec.id,
          },
        ]);
      },
    }
  );

  const stage = (transfer: DataTransfer | null, e: React.SyntheticEvent) => {
    const { accepted, rejected } = filesFromTransfer(transfer);
    if (accepted.length === 0) {
      // A paste carrying files we can't take says so; one carrying no files at
      // all is ordinary text and still belongs to the textarea.
      setError(rejectedFilesMessage(rejected));
      return;
    }
    e.preventDefault();
    setError(null);
    setFiles(current => [...current, ...accepted.map(file => ({ file }))]);
  };

  const submit = () => {
    // A photo with no words is a real entry — and the one that most needs its
    // title generated from a caption. So is a recording with no words typed.
    if (!content.trim() && files.length === 0) return;
    onSubmit(content, files);
    setContent('');
    setFiles([]);
  };

  return (
    <div
      className="mb-4 p-4 bg-[var(--color-surface)] rounded-lg border border-white/10"
      // The entry does not exist server-side yet, so a file pasted here is
      // held until the create succeeds and uploaded then.
      onPaste={e => stage(e.clipboardData, e)}
      onDrop={e => stage(e.dataTransfer, e)}
      onDragOver={e => {
        if (e.dataTransfer?.types?.includes('Files')) e.preventDefault();
      }}
    >
      <textarea
        value={content}
        onChange={e => setContent(e.target.value)}
        autoFocus
        onKeyDown={e => {
          if (e.key === 'Escape') {
            onCancel();
            return;
          }
          // Enter saves; Shift+Enter inserts a newline.
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder="Write your journal entry..."
        rows={4}
        className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none"
      />
      <AttachmentButtons
        idPrefix="journal-new-entry"
        onFiles={picked => {
          setError(null);
          setFiles(current => [...current, ...picked.map(file => ({ file }))]);
        }}
        extra={
          <button
            type="button"
            onClick={() => {
              if (recorder.status === 'recording') recorder.stop();
              else if (recorder.status === 'idle') void recorder.start('audio');
            }}
            disabled={
              recorder.status === 'transcribing' || recorder.status === 'saving'
            }
            data-testid="journal-new-entry-transcribe"
            title={
              recorder.canTranscribe
                ? 'Record — the audio is attached to the entry and transcribed into it'
                : 'Offline — the recording is kept and attached on save, but there is no transcript until the server is back'
            }
            aria-label={
              recorder.status === 'recording'
                ? 'Stop recording'
                : 'Transcribe into this entry'
            }
            className={`px-2 py-1 text-xs rounded border ${
              recorder.status === 'recording'
                ? 'border-red-500/40 bg-red-500/25 text-red-300'
                : 'border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-white/20'
            } disabled:opacity-50`}
          >
            {recorder.status === 'recording'
              ? '■ Stop'
              : recorder.status === 'transcribing'
                ? 'Transcribing…'
                : recorder.status === 'saving'
                  ? 'Saving…'
                  : '● Transcribe'}
          </button>
        }
      />
      {recorder.error && (
        <p className="mt-2 text-xs text-red-400">{recorder.error}</p>
      )}
      {!recorder.error && recorderNotice && (
        <p className="mt-2 text-xs text-amber-300">{recorderNotice}</p>
      )}
      {error && (
        <div className="mt-2 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded text-sm text-red-400">
          {error}
        </div>
      )}
      {files.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2">
          {files.map((s, i) => (
            <span
              key={`${s.file.name}:${i}`}
              className="flex items-center gap-1 px-2 py-0.5 text-xs rounded border border-white/20 text-[var(--color-text-muted)] bg-white/5"
            >
              {s.file.name || 'attachment'}
              <button
                onClick={() => {
                  // Discarding a staged recording is one of the two places the
                  // audio may be let go of without the server having seen it —
                  // the user said to.
                  if (s.recordingId) void deleteRecording(s.recordingId);
                  setFiles(current =>
                    current.filter((_, index) => index !== i)
                  );
                }}
                aria-label={`Remove ${s.file.name || 'attachment'}`}
                className="text-red-400 hover:text-red-300"
              >
                ×
              </button>
            </span>
          ))}
          <span className="text-xs text-[var(--color-text-muted)] self-center">
            attached on save
          </span>
        </div>
      )}
      <div className="flex justify-end gap-2 mt-2">
        <button
          onClick={() => {
            // Cancelling is an explicit discard, so the stored audio goes with
            // it rather than being rescued into an entry of its own later.
            for (const st of files) {
              if (st.recordingId) void deleteRecording(st.recordingId);
            }
            setFiles([]);
            onCancel();
          }}
          className="px-3 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          Cancel
        </button>
        <button
          onClick={submit}
          disabled={!content.trim() && files.length === 0}
          className="px-3 py-1 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
        >
          Save
        </button>
      </div>
    </div>
  );
}

// Shown in edit mode for an entry that's nothing but a single recording
// (isVoiceOnlyEntry): offers to fold that recording into another entry from
// the same day instead of the recording living as its own bare entry. Only
// entries from the same local day are candidates — the backend enforces this
// too (backend/routes/journal.py's merge route), this just keeps the picker
// from offering something the server would reject.
function MergeIntoPicker({
  entry,
  onMerged,
}: {
  entry: JournalEntry;
  onMerged: () => void;
}) {
  const queryClient = useQueryClient();
  const [targetId, setTargetId] = useState('');

  const {
    data: candidates,
    isLoading,
    isError,
    error: candidatesError,
  } = useQuery({
    queryKey: ['journal', 'mergeCandidates', entry.id],
    queryFn: () => api.journal.mergeCandidates(entry.id),
  });

  const merge = useMutation({
    mutationFn: (targetId: string) => api.journal.merge(entry.id, targetId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['journal'] });
      onMerged();
    },
  });

  if (isLoading) return null;

  // Surfaced rather than swallowed: a request that 404s (e.g. a backend that
  // hasn't picked up this route yet) used to look identical to "no other
  // entries today" — silence either way — which made the feature look absent
  // instead of broken.
  if (isError) {
    return (
      <div className="mt-3 px-3 py-2 rounded border border-red-500/20 bg-red-500/10 text-xs text-red-400">
        Couldn't check for other entries to merge into:{' '}
        {(candidatesError as Error).message}
      </div>
    );
  }

  if (!candidates || candidates.length === 0) return null;

  return (
    <div className="mt-3 p-2 rounded border border-white/10 bg-white/5">
      <div className="text-xs text-[var(--color-text-muted)] mb-1.5">
        Just a recording — attach it to another entry from today instead?
      </div>
      <div className="flex items-center gap-2">
        <select
          value={targetId}
          onChange={e => setTargetId(e.target.value)}
          aria-label="Entry to merge into"
          className="flex-1 min-w-0 bg-[var(--color-surface)] border border-white/10 rounded px-2 py-1 text-sm text-[var(--color-text)]"
        >
          <option value="">Choose an entry…</option>
          {candidates.map(c => (
            <option key={c.id} value={c.id}>
              {(c.title || c.content || '(untitled)').slice(0, 60)}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => targetId && merge.mutate(targetId)}
          disabled={!targetId || merge.isPending}
          className="shrink-0 px-2 py-1 text-xs rounded border border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-white/20 disabled:opacity-50"
        >
          {merge.isPending ? 'Merging…' : 'Merge'}
        </button>
      </div>
      {merge.isError && (
        <div className="mt-1.5 text-xs text-red-400">
          {(merge.error as Error).message}
        </div>
      )}
    </div>
  );
}

// Clips from the STT listener's Journal hotkey that haven't resolved into an
// entry yet — still being cross-checked by several local STT models, or
// stuck in error. Open by default: this is meant to be noticed, not dug for.
function VoiceDraftsPanel({ drafts }: { drafts: JournalVoiceDraft[] }) {
  const queryClient = useQueryClient();
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['journal', 'voiceDrafts'] });

  const retryDraft = useMutation({
    mutationFn: (id: string) => api.journal.voiceDrafts.retry(id),
    onSuccess: invalidate,
  });
  const deleteDraft = useMutation({
    mutationFn: (id: string) => api.journal.voiceDrafts.delete(id),
    onSuccess: invalidate,
  });

  const processingCount = drafts.filter(d => d.status === 'processing').length;
  const errorCount = drafts.filter(d => d.status === 'error').length;

  return (
    <details
      open
      className="mb-4 p-3 bg-[var(--color-surface)] rounded-lg border border-white/10"
    >
      <summary className="text-sm text-[var(--color-text-muted)] cursor-pointer select-none hover:text-[var(--color-text)] transition-colors">
        🎙 Voice drafts
        {processingCount > 0 && ` · ${processingCount} processing`}
        {errorCount > 0 && ` · ${errorCount} failed`}
      </summary>
      <div className="mt-2 flex flex-col gap-2">
        {drafts.map(d => (
          <div key={d.id} className="flex items-center gap-2">
            {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
            <audio src={d.url} controls className="h-8 flex-1 min-w-0" />
            {d.status === 'processing' && (
              <span className="text-xs text-[var(--color-text-muted)] whitespace-nowrap">
                Processing…
              </span>
            )}
            {d.status === 'error' && (
              <>
                <span
                  className="text-xs text-red-400 truncate max-w-[16rem]"
                  title={d.error ?? undefined}
                >
                  {d.error || 'Failed'}
                </span>
                <button
                  onClick={() => retryDraft.mutate(d.id)}
                  disabled={retryDraft.isPending}
                  className="px-2 py-0.5 text-xs rounded border border-white/20 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-white/40 transition-colors disabled:opacity-50"
                >
                  Retry
                </button>
                <button
                  onClick={() => deleteDraft.mutate(d.id)}
                  disabled={deleteDraft.isPending}
                  className="px-2 py-0.5 text-xs rounded border border-white/20 text-[var(--color-text-muted)] hover:text-red-400 hover:border-red-400/40 transition-colors disabled:opacity-50"
                >
                  Discard
                </button>
              </>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}

// `duplicate` is legacy: briefings written before linking existed can still
// carry it. `pending` covers an item that was never resolved before the
// accept/reject card retired — there is no backend left to act on it, so it
// just shows as still-open history.
const PROPOSED_TODO_STATUS_LABEL: Record<string, string> = {
  pending: 'Not resolved',
  done: 'Done',
  accepted: 'Added to to-dos',
  rejected: 'Dismissed',
  duplicate: 'Already on your list — skipped',
};

// A frozen, read-only rendering of a past briefing's plan for the day. The
// accept/reject card (BriefingTodos.tsx) that used to sit here is retired —
// the briefing now writes straight into the Chat tab's to-do bar with no
// confirm step — but the day's plan is still the record of what actually got
// done, so old messages' resolved status is kept visible rather than dropped.
function ProposedTodoHistory({ items }: { items: ProposedTodo[] }) {
  return (
    <div className="mt-2 space-y-1 rounded-lg border border-white/10 bg-[var(--color-surface)]/60 p-2">
      {items.map(p => (
        <div
          key={p.id}
          className="flex items-baseline justify-between gap-2 px-1 py-0.5"
        >
          <span
            className={`text-sm ${p.status === 'rejected' ? 'text-[var(--color-text-muted)] line-through' : 'text-[var(--color-text)]'}`}
          >
            {p.title}
          </span>
          <span className="text-xs text-[var(--color-text-muted)] shrink-0">
            {PROPOSED_TODO_STATUS_LABEL[p.status] ?? p.status}
            {p.resolvedAt &&
              ` · ${formatCompletedAt(new Date(p.resolvedAt * 1000).toISOString())}`}
          </span>
        </div>
      ))}
    </div>
  );
}

// A saved chat day in the journal feed: collapsed by default (chats get long),
// dimmed like transcriptions, with its full transcript lazily fetched on expand.
// memo'd, like the two feed items below it. These render once per row in an
// unbounded feed and none of them depends on anything the composer or the
// search box changes; without memo they all re-rendered whenever Journal did.
// SavedChatItem is the expensive one — an expanded chat re-runs react-markdown
// over every message in it.
const SavedChatItem = memo(function SavedChatItem({
  conversation,
}: {
  conversation: DatedConversation;
}) {
  const [open, setOpen] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ['chat', 'conversation', conversation.id],
    queryFn: () => api.chat.getConversation(conversation.id),
    enabled: open,
  });

  const dayLabel = formatDay(conversation.dayKey + 'T00:00:00');
  const isWebSearch = conversation.mode === 'websearch';
  const title =
    conversation.title ||
    `${isWebSearch ? 'Web search' : 'Chat'} — ${dayLabel}`;
  const messages = data?.messages ?? [];

  return (
    <details
      className="p-3 bg-[var(--color-surface)]/50 rounded-lg border border-white/5 opacity-70"
      onToggle={e => setOpen((e.target as HTMLDetailsElement).open)}
    >
      <summary className="cursor-pointer select-none list-none flex items-baseline justify-between gap-2">
        <span className="text-[var(--color-text)] truncate">
          {isWebSearch ? '🔎' : '💬'} {title}
        </span>
        <span className="text-xs text-[var(--color-text-muted)] shrink-0">
          {dayLabel} · {conversation.messageCount} msg
          {conversation.messageCount === 1 ? '' : 's'}
        </span>
      </summary>
      <div className="mt-3 space-y-2">
        {isLoading && (
          <div className="text-sm text-[var(--color-text-muted)]">Loading…</div>
        )}
        {messages.map(m => {
          if (m.role === 'system') {
            if (!isBreak(m)) return null;
            return (
              <div
                key={m.id}
                className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)] text-center py-1"
              >
                New chat
              </div>
            );
          }
          // The day's plan is the record of what actually got done, so it has
          // to survive the chat-day rollover into the feed's history.
          const proposedTodos = parseProposedTodos(m.metadata);
          return (
            <div
              key={m.id}
              className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`content-text max-w-[85%] rounded-lg px-3 py-1.5 text-sm ${m.role === 'user' ? 'bg-[var(--color-primary)]/80 text-white' : 'bg-white/5 text-[var(--color-text)]'}`}
              >
                {m.role === 'user' ? (
                  <div className="whitespace-pre-wrap">{m.content}</div>
                ) : (
                  <MessageMarkdown content={m.content} />
                )}
                {proposedTodos.length > 0 && (
                  <ProposedTodoHistory items={proposedTodos} />
                )}
              </div>
            </div>
          );
        })}
      </div>
    </details>
  );
});

// An archived paper (drawings) in the journal feed: a header with the day and a
// horizontal filmstrip of page thumbnails. Tapping a page opens it full-screen.
// View-only here — the paper is a record of what was drawn that day.
const JournalPaperItem = memo(function JournalPaperItem({
  paper,
}: {
  paper: JournalPaper;
}) {
  const lightbox = useLightbox();

  const dayLabel = formatDay(paper.journalDate + 'T00:00:00');
  const title = paper.title || `Drawings — ${dayLabel}`;
  const pages = paper.pages.filter(pg => pg.imageUrl);

  return (
    <div className="p-3 bg-[var(--color-surface)]/50 rounded-lg border border-white/5">
      <div className="flex items-baseline justify-between gap-2 mb-2">
        <span className="text-[var(--color-text)] truncate">🖊 {title}</span>
        <span className="text-xs text-[var(--color-text-muted)] shrink-0">
          {dayLabel} · {paper.pages.length} page
          {paper.pages.length === 1 ? '' : 's'}
        </span>
      </div>
      <div className="flex gap-2 overflow-x-auto pb-1">
        {pages.map(pg => (
          <button
            key={pg.id}
            onClick={() => lightbox.open(pg.imageUrl)}
            className="shrink-0 h-32 rounded-md overflow-hidden border border-white/10 bg-white hover:border-[var(--color-primary)] transition-colors"
            title="View"
          >
            {/* Fixed height, width follows the page — a landscape page used to
                be cropped to its top-left corner. */}
            <img
              src={pg.imageUrl!}
              alt=""
              className="h-full w-auto object-contain"
            />
          </button>
        ))}
        {pages.length === 0 && (
          <span className="text-sm text-[var(--color-text-muted)] italic">
            No pages
          </span>
        )}
      </div>

      <ImageLightbox src={lightbox.src} onClose={lightbox.close} whiteBg />
    </div>
  );
});

const TASK_LIST_LABELS: Record<string, string> = {
  todo: 'To-Do',
  chores: 'Chores',
  archive: 'Archive',
  daily: 'Daily',
};

// A task completion/deletion in the journal feed: a small one-line notification,
// deliberately much smaller than an entry card. Non-selectable (a passive log).
// The list it came from (To-Do / Chores / Archive / Daily) is shown as a pill.
// Clicking expands it (like a to-do row) to reveal the task's saved notes.
function JournalTaskEventItem({
  event,
  formatDate,
}: {
  event: TaskEvent;
  formatDate: (date: string) => string;
}) {
  const [expanded, setExpanded] = useState(false);
  const removed = event.kind === 'task_deleted';
  const listLabel = event.taskList ? TASK_LIST_LABELS[event.taskList] : null;
  return (
    <button
      type="button"
      onClick={() => setExpanded(v => !v)}
      className="w-full text-left px-3 py-1.5 rounded hover:bg-white/5 transition-colors"
    >
      <div className="flex items-baseline gap-2 text-xs text-[var(--color-text-muted)]">
        <span
          className={
            removed ? 'text-red-400/70' : 'text-[var(--color-primary)]'
          }
        >
          {removed ? '✕' : '✓'}
        </span>
        <span className="min-w-0 truncate">
          {removed ? 'Removed' : 'Completed'}:{' '}
          <span className="text-[var(--color-text)]">{event.title}</span>
        </span>
        {listLabel && (
          <span className="shrink-0 px-1.5 py-0.5 rounded border border-white/15 text-[10px] uppercase tracking-wide opacity-80">
            {listLabel}
          </span>
        )}
        <span className="ml-auto shrink-0 opacity-70">
          {formatDate(event.createdAt)}
        </span>
      </div>
      {expanded && (
        <div className="mt-1 pl-5 text-xs text-[var(--color-text-muted)]">
          {event.detail ? (
            <span className="whitespace-pre-wrap">{event.detail}</span>
          ) : (
            <span className="italic opacity-70">No additional notes.</span>
          )}
        </div>
      )}
    </button>
  );
}

// A food-log entry in the journal feed: dish/place/rating with a media
// filmstrip. View-only here — editing happens in the Food tab.
const JournalFoodItem = memo(function JournalFoodItem({
  food,
}: {
  food: FoodJournalItem;
}) {
  const lightbox = useLightbox();
  const stars = ratingStars(food.rating);
  const geoLink = mapLink(food.latitude, food.longitude);

  const dayLabel = formatDayTime(food.createdAt);

  return (
    <div className="p-3 bg-[var(--color-surface)]/50 rounded-lg border border-white/5">
      <div className="flex items-baseline justify-between gap-2 mb-2">
        <span className="text-[var(--color-text)] truncate">
          🍽 {foodTitle(food)}
          {food.place && (
            <span className="text-[var(--color-text-muted)]">
              {' '}
              · 📍 {food.place}
            </span>
          )}
          {geoLink && (
            <a
              href={geoLink}
              target="_blank"
              rel="noreferrer"
              onClick={e => e.stopPropagation()}
              className="text-[var(--color-text-muted)] underline hover:text-[var(--color-text)]"
            >
              {' '}
              · 🗺️ map
            </a>
          )}
        </span>
        <span className="text-xs text-[var(--color-text-muted)] shrink-0">
          {stars && (
            <span className="text-[var(--color-primary)] mr-2">{stars}</span>
          )}
          {dayLabel}
        </span>
      </div>

      {food.notes && (
        <div className="text-sm text-[var(--color-text)] whitespace-pre-wrap mb-2">
          {food.notes}
        </div>
      )}

      {food.media.length > 0 && (
        <div className="flex gap-2 overflow-x-auto pb-1">
          {food.media.map(m =>
            m.kind === 'video' ? (
              <video
                key={m.id}
                src={m.url}
                controls
                className="shrink-0 h-28 rounded-md border border-white/10"
              />
            ) : (
              <button
                key={m.id}
                onClick={() => lightbox.open(m.url)}
                className="shrink-0 h-28 rounded-md overflow-hidden border border-white/10 hover:border-[var(--color-primary)] transition-colors"
                title="View"
              >
                {/* Fixed height, width follows the photo — a wide meal shot in
                    a square box lost its edges. */}
                <img
                  src={m.url}
                  alt=""
                  className="h-full w-auto object-contain"
                />
              </button>
            )
          )}
        </div>
      )}

      {food.recipe && (
        <div className="mt-2">
          <span className="px-2 py-0.5 text-xs rounded border border-[var(--color-primary)]/40 text-[var(--color-primary)] bg-[var(--color-primary)]/10">
            🍳 {food.recipe.title}
          </span>
        </div>
      )}

      <ImageLightbox src={lightbox.src} onClose={lightbox.close} />
    </div>
  );
});
