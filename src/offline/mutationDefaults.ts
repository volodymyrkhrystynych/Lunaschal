import {
  useMutation,
  useQueryClient,
  type InfiniteData,
  type QueryClient,
  type UseMutationOptions,
} from '@tanstack/react-query';
import {
  api,
  ApiError,
  NetworkError,
  type CalorieDay,
  type CalorieLog,
  type ClaimCoverage,
  type DailyTask,
  type FoodEntry,
  type JournalAttachment,
  type IdeaSummary,
  type JournalEntry,
  type PaperDetail,
  type PaperPageContent,
  type PaperPageImage,
  type Recipe,
  type Selfie,
  type TodoItem,
  type TodoPayload,
} from '../hooks/api';
import {
  assembleBlob,
  deleteRecording,
  getRecording,
  markAttempt,
} from './recordingStore';
import {
  deletePhoto,
  getPhoto,
  markAttempt as markPhotoAttempt,
} from './photoStore';
import { clearPageSave, getPageSave } from './pageStore';

/**
 * Offline write queue.
 *
 * These mutations use `networkMode: 'online'`, so while the backend is
 * unreachable react-query *pauses* them (instead of erroring) and replays them
 * via `resumePausedMutations()` on reconnect. Everything NOT listed here keeps
 * react-query's global `networkMode: 'always'` default (set in main.tsx), which
 * fires and fails fast offline (never pausing/replaying) — that's deliberately
 * how deletes, AI calls and heavy pipelines stay online-only with no per-call
 * work.
 *
 * Each mutation's behavior (function, optimistic `onMutate`, reconciling
 * `onSettled`) is defined once as a `*Cfg(qc)` builder. The builder is used in
 * two places:
 *   - the `use*` hooks apply it inline (so components work standalone), and
 *   - `registerOfflineMutationDefaults` registers it under the mutationKey so a
 *     mutation paused before a page reload can still be reconstructed and
 *     replayed by `resumePausedMutations()`, when no component is mounted.
 * A component's own `onSuccess`/`onError` (UI-only concerns) layer on top.
 */

export const MUTATION_KEYS = {
  journalCreate: ['journal', 'create'] as const,
  journalUpdate: ['journal', 'update'] as const,
  journalRecording: ['journal', 'recording'] as const,
  todoCreate: ['todos', 'create'] as const,
  todoUpdate: ['todos', 'update'] as const,
  dailyToggle: ['tasks', 'toggle'] as const,
  fanficProgress: ['fanfic', 'progress'] as const,
  fanficSetRead: ['fanfic', 'setRead'] as const,
  writingChapterUpdate: ['writing', 'chapter', 'update'] as const,
  writingNoteUpdate: ['writing', 'note', 'update'] as const,
  learningAttempt: ['learning', 'attempt'] as const,
  learningReview: ['learning', 'review'] as const,
  notebookWrite: ['notebook', 'write'] as const,
  calendarCreate: ['calendar', 'create'] as const,
  ideaCreate: ['ideas', 'create'] as const,
  calorieLog: ['lifestyle', 'calories', 'create'] as const,
  foodCreate: ['food', 'create'] as const,
  selfieUpload: ['lifestyle', 'selfie', 'upload'] as const,
  paperCreate: ['paper', 'create'] as const,
  paperPageAdd: ['paper', 'page', 'add'] as const,
  paperPageSave: ['paper', 'page', 'save'] as const,
  paperImageAdd: ['paper', 'image', 'add'] as const,
  paperImageUpdate: ['paper', 'image', 'update'] as const,
  recipeCreate: ['cookbook', 'create'] as const,
};

// --- variable shapes (self-contained so a reloaded replay can run them) ---

export interface JournalCreateVars {
  id: string;
  content: string;
  title?: string;
  tags?: string[];
  /**
   * How many files the composer is about to upload against this id, so the
   * server's title generation waits for their captions. A plain number, which
   * is what makes it safe here: these vars are structured-cloned into IndexedDB
   * and a paused create has to be replayable from them alone (the Files
   * themselves are not, and are not stored — a create replayed after a reload
   * simply arrives with nothing to wait for, and titles from its text).
   */
  pendingAttachments?: number;
}
export interface JournalUpdateVars {
  id: string;
  content: string;
  title: string;
}
/**
 * Note what is *not* here: the audio. The persister structured-clones the whole
 * query client on every write, so a Blob in the payload would be copied on every
 * unrelated mutation — and a paused mutation restored after a reload has to be
 * reconstructable from these vars alone. The id is enough: the blob is fetched
 * from recordingStore inside the mutationFn.
 */
export interface JournalRecordingVars {
  id: string;
  name?: string;
  /**
   * Attach to this entry instead of creating one. Omitted by the bottom bar's
   * buttons, where the recording *is* the entry; set by the Journal's Transcribe
   * button, which records while an entry is already open.
   *
   * An id, not the entry object — these vars are structured-cloned into the
   * persisted cache on every unrelated write and a paused upload has to be
   * replayable from them alone after a reload, which is the same reason the
   * audio itself is not here.
   */
  entryId?: string;
  /**
   * Also capture this clip as an idea, under this id — the Ideas tab's Record
   * button. One upload, one transcription, two rows; see
   * `create_recording_entry` in backend/routes/journal.py.
   */
  ideaId?: string;
  /** Which repository that idea belongs to; omitted means the default. */
  repoId?: string;
}
export type TodoCreateVars = TodoPayload & { title: string; id: string };
export interface TodoUpdateVars {
  id: string;
  data: TodoPayload;
}
export interface DailyToggleVars {
  id: string;
  done: boolean;
}
export interface FanficProgressVars {
  ficId: string;
  chapterId: string;
}
export interface FanficSetReadVars {
  ficId: string;
  ids: string[];
  read: boolean;
}
export interface WritingChapterUpdateVars {
  chapterId: string;
  title?: string;
  content?: string;
}
export interface WritingNoteUpdateVars {
  noteId: string;
  title?: string;
  content?: string;
  docType?: string;
}
export interface LearningAttemptVars {
  id: string;
  cardId: string;
  mode: 'answered' | 'skipped';
  answer?: string;
  answerMode?: 'typed' | 'voice';
  speechMode?: boolean;
}
export interface LearningReviewVars {
  cardId: string;
  reviewId: string;
  rating: number;
  suggestedRating?: number;
  userAnswer?: string;
  coverage?: ClaimCoverage;
  answerMode?: 'typed' | 'voice' | 'self';
}
export interface NotebookWriteVars {
  path: string;
  content: string;
}
/** Every one of these carries the id the browser minted, for the same reason
 * JournalCreateVars does: the row's identity has to exist before the server
 * does, or a replay cannot be told apart from a second capture. */
export type CalendarCreateVars = Parameters<typeof api.calendar.create>[0] & {
  id: string;
};
export interface IdeaCreateVars {
  id: string;
  title?: string;
  rawContent?: string;
  tags?: string[];
  /** Omit to let the server stamp the registered default repository. */
  repoId?: string;
}
export interface CalorieLogVars {
  id: string;
  date?: string;
  description: string;
  calories: number;
}
/**
 * Note what is *not* here, for the same reason `JournalRecordingVars` omits the
 * audio: the photos. Vars are structured-cloned into the persisted cache on
 * every unrelated write, so they carry ids and the blobs are fetched from
 * `photoStore` inside the mutationFn.
 */
export interface FoodCreateVars {
  id: string;
  photoIds: string[];
  text?: string;
  latitude?: number;
  longitude?: number;
}
export interface SelfieUploadVars {
  photoId: string;
  date?: string;
}
export interface PaperCreateVars {
  id: string;
  pageId: string;
}
export interface PaperPageAddVars {
  paperId: string;
  pageId: string;
}

/**
 * Everything paper does, in one lane.
 *
 * Mutations sharing a `scope.id` run one at a time, in the order they were
 * started — and paper's writes depend on each other: a page belongs to a paper,
 * a saved page and a pasted picture belong to a page. Replayed in parallel
 * (which is what `resumePausedMutations` does otherwise) a page-add can reach
 * the server before the paper it belongs to exists, and answer 404 — which is
 * not a network failure, so nothing would retry it and the page would be gone.
 *
 * One lane for the whole feature rather than one per paper: the queue is a
 * tablet's afternoon, not a fleet's, and a single ordering is easier to reason
 * about than several that can interleave.
 */
const PAPER_LANE = { scope: { id: 'paper' } };
/** A page id and nothing else: the payload is read from `pageStore` when the
 *  upload actually runs, so a page written on all afternoon uploads once, with
 *  the afternoon's final state rather than its first. */
export interface PaperPageSaveVars {
  pageId: string;
}
export interface PaperImageAddVars {
  imageId: string;
  pageId: string;
  box: { x: number; y: number; width: number; height: number };
  filename: string;
}
export interface PaperImageUpdateVars {
  imageId: string;
  pageId: string;
  edit: Partial<{
    x: number;
    y: number;
    width: number;
    height: number;
    rotation: number;
    flipped: boolean;
    locked: boolean;
  }>;
}
export interface RecipeCreateVars {
  id: string;
  title: string;
  content: string;
  tags?: string[];
  photoIds: string[];
}

// The behavioral slice of a mutation's options that both the hook and the
// registered default share.
type Cfg<TData, TVars> = Pick<
  UseMutationOptions<TData, Error, TVars>,
  | 'networkMode'
  | 'mutationFn'
  | 'onMutate'
  | 'onSuccess'
  | 'onSettled'
  | 'retry'
  | 'retryDelay'
  | 'scope'
>;

/**
 * The shared shape of a queueable write: paused while the backend is known to
 * be unreachable, and retried when a request discovers that it is.
 *
 * The retry is not belt-and-braces, it is the whole hinge. React Query pauses a
 * mutation *before* firing it, by asking whether we are online — so a write
 * issued in the moment the link drops, before anything has noticed, fires and
 * fails at the network level instead. With no retry that mutation went straight
 * to `error`, `onSettled` invalidated, and the optimistic entry the user had
 * just watched appear was rolled back off the screen. The write was gone.
 *
 * Now the failing request itself reports the backend unreachable
 * (`reportFetchOutcome`), so by the time the retry is scheduled we are offline
 * and React Query parks the mutation in the queue instead of running it —
 * which is where it should have gone in the first place.
 *
 * Only network failures qualify. An `ApiError` means the server answered and
 * refused: replaying that just gets refused again, and hiding it behind a retry
 * would turn a real error into a silent one.
 */
const ONLINE = {
  networkMode: 'online' as const,
  retry: (failureCount: number, error: Error) =>
    error instanceof NetworkError && failureCount < 3,
  // Long enough for the online manager to have settled the verdict the failure
  // above reported, short enough that a blip costs nothing.
  retryDelay: 300,
};

// --- optimistic cache updaters (only for cache-driven UIs whose shapes we
// know; queue-only mutations reconcile on reconnect via onSettled) ---

// The object-keyed journal list is an infinite query, so its cache is
// InfiniteData<JournalEntry[]>. `firstPageOnly` (used for inserts) applies fn to
// the newest page only so a new entry lands once at the top; the default (used
// for edits) maps every page since the target lives in exactly one of them.
type JournalListCache = JournalEntry[] | InfiniteData<JournalEntry[]>;

function isInfinite(v: JournalListCache): v is InfiniteData<JournalEntry[]> {
  return typeof v === 'object' && v !== null && 'pages' in v;
}

function patchJournalLists(
  qc: QueryClient,
  fn: (list: JournalEntry[]) => JournalEntry[],
  opts?: { firstPageOnly?: boolean }
) {
  const firstPageOnly = opts?.firstPageOnly ?? false;
  qc.setQueriesData<JournalListCache>(
    {
      predicate: q =>
        q.queryKey[0] === 'journal' && typeof q.queryKey[1] === 'object',
    },
    old => {
      if (!old) return old;
      if (isInfinite(old)) {
        return {
          ...old,
          pages: old.pages.map((p, i) =>
            firstPageOnly ? (i === 0 ? fn(p) : p) : fn(p)
          ),
        };
      }
      return fn(old);
    }
  );
}

// --- per-mutation config builders ---

const journalCreateCfg = (
  qc: QueryClient
): Cfg<{ id: string }, JournalCreateVars> => ({
  ...ONLINE,
  mutationFn: vars => api.journal.create(vars),
  onMutate: vars => {
    const nowIso = new Date().toISOString();
    const entry: JournalEntry = {
      id: vars.id,
      content: vars.content,
      rawContent: null,
      title: vars.title ?? null,
      tags: vars.tags ? JSON.stringify(vars.tags) : null,
      curatedTags: [],
      createdAt: nowIso,
      updatedAt: nowIso,
    };
    patchJournalLists(qc, list => [entry, ...list], { firstPageOnly: true });
  },
  onSettled: () => qc.invalidateQueries({ queryKey: ['journal'] }),
});

const journalUpdateCfg = (
  qc: QueryClient
): Cfg<{ success: boolean }, JournalUpdateVars> => ({
  ...ONLINE,
  mutationFn: vars =>
    api.journal.update(vars.id, { content: vars.content, title: vars.title }),
  onMutate: vars => {
    const nowIso = new Date().toISOString();
    patchJournalLists(qc, list =>
      list.map(e =>
        e.id === vars.id
          ? {
              ...e,
              content: vars.content,
              title: vars.title,
              updatedAt: nowIso,
            }
          : e
      )
    );
  },
  onSettled: () => qc.invalidateQueries({ queryKey: ['journal'] }),
});

/** A 4xx means the server has looked at this recording and will not take it.
 *  Retrying changes nothing — but the audio is still kept, and offered for
 *  download, because "the server refused it" is not a reason to destroy it. */
function isTerminal(error: unknown): boolean {
  return error instanceof ApiError && error.status >= 400 && error.status < 500;
}

type RecordingResult = { id: string; attachment: JournalAttachment };

const journalRecordingCfg = (
  qc: QueryClient
): Cfg<RecordingResult, JournalRecordingVars> => ({
  ...ONLINE,
  retry: (failureCount, error) => !isTerminal(error) && failureCount < 5,
  retryDelay: attempt => Math.min(30_000, 1000 * 2 ** attempt),
  mutationFn: async vars => {
    const rec = await getRecording(vars.id);
    if (!rec) throw new Error('That recording is no longer on this device.');
    const blob = await assembleBlob(vars.id);
    if (!blob || blob.size === 0) {
      // Nothing was ever captured (permission revoked before the first chunk).
      // There is no audio to protect, so clear it out rather than leaving an
      // un-uploadable row in the pending list forever.
      await deleteRecording(vars.id);
      throw new Error('That recording was empty.');
    }
    try {
      // The recording id is used for both the entry and the attachment, which
      // is what makes a replay a no-op server-side.
      const res = await api.journal.createRecording(blob, {
        // `entryId` when the clip belongs to an entry that already exists; the
        // recording's own id otherwise, which is what makes a fresh recording
        // entry and its attachment share one id and a replay a no-op.
        id: vars.entryId ?? vars.id,
        attachmentId: vars.id,
        name: vars.name,
        transcribe: rec.mode === 'transcribe',
        ideaId: vars.ideaId,
        repoId: vars.repoId,
      });
      // Confirmed stored. This is the only place the audio may be let go of.
      await deleteRecording(vars.id);
      return res;
    } catch (e) {
      await markAttempt(
        vars.id,
        e instanceof Error ? e.message : 'Upload failed',
        isTerminal(e)
      );
      throw e;
    }
  },
  onMutate: vars => {
    // An Ideas capture shows up in the Ideas list the moment recording stops,
    // for the same reason the journal entry does below: stopping *is* the save,
    // and a list that stays empty until the upload lands reads as a lost idea.
    // Empty of text on purpose — the transcript is minutes away, and inventing
    // a placeholder title here would be a guess the user then has to delete.
    if (vars.ideaId) insertPendingIdea(qc, vars.ideaId, vars.repoId);
    // Nothing to insert when the clip is being attached to an entry that is
    // already in the feed — an optimistic row here would show a duplicate of
    // the entry the user is looking at, then vanish on the next refetch.
    if (vars.entryId) return;
    // The entry appears in the feed the moment recording stops, even offline —
    // "I recorded it and the journal is empty" was half the reported bug.
    const nowIso = new Date().toISOString();
    const entry: JournalEntry = {
      id: vars.id,
      content: '',
      rawContent: null,
      title: vars.name ?? null,
      tags: null,
      curatedTags: [],
      ideaId: vars.ideaId ?? null,
      ideaTitle: null,
      createdAt: nowIso,
      updatedAt: nowIso,
    };
    patchJournalLists(qc, list => [entry, ...list], { firstPageOnly: true });
  },
  onSettled: (_data, _err, vars) => {
    qc.invalidateQueries({ queryKey: ['journal'] });
    // The transcript fills the idea in as well, so the list it is sitting in
    // has to be refetched too — the optimistic row above carries no text.
    if (vars.ideaId) qc.invalidateQueries({ queryKey: ['ideas'] });
  },
});

const todoCreateCfg = (
  qc: QueryClient
): Cfg<{ id: string }, TodoCreateVars> => ({
  ...ONLINE,
  mutationFn: vars => api.todos.create(vars),
  onMutate: vars => {
    const nowIso = new Date().toISOString();
    const todo: TodoItem = {
      id: vars.id,
      title: vars.title,
      done: false,
      completedAt: null,
      list: vars.list ?? 'todo',
      notes: vars.notes ?? null,
      due: null,
      repeatInterval: vars.repeatInterval ?? null,
      repeatUnit: vars.repeatUnit ?? null,
      priority: vars.priority ?? 3,
      createdAt: nowIso,
      updatedAt: nowIso,
    };
    qc.setQueryData<TodoItem[]>(['todos'], old => (old ? [todo, ...old] : old));
  },
  onSettled: () => qc.invalidateQueries({ queryKey: ['todos'] }),
});

const todoUpdateCfg = (
  qc: QueryClient
): Cfg<{ success: boolean }, TodoUpdateVars> => ({
  ...ONLINE,
  mutationFn: vars => api.todos.update(vars.id, vars.data),
  onMutate: vars => {
    // `due` in the payload is a unix int, but the cached TodoItem holds an
    // ISO string — skip it optimistically; the reconciling refetch fixes it.
    const { due, ...rest } = vars.data;
    void due;
    qc.setQueryData<TodoItem[]>(['todos'], old =>
      old?.map(t => (t.id === vars.id ? { ...t, ...rest } : t))
    );
  },
  onSettled: () => {
    qc.invalidateQueries({ queryKey: ['todos'] });
    // Completing/un-completing a todo adds/retracts a Journal notification.
    qc.invalidateQueries({ queryKey: ['taskEvents'] });
  },
});

const dailyToggleCfg = (
  qc: QueryClient
): Cfg<{ success: boolean }, DailyToggleVars> => ({
  ...ONLINE,
  mutationFn: vars =>
    vars.done ? api.tasks.uncomplete(vars.id) : api.tasks.complete(vars.id),
  onMutate: vars => {
    qc.setQueryData<DailyTask[]>(['tasks'], old =>
      old?.map(t => (t.id === vars.id ? { ...t, done: !vars.done } : t))
    );
  },
  onSettled: () => {
    qc.invalidateQueries({ queryKey: ['tasks'] });
    // Completing/un-completing a daily task adds/retracts a Journal notification.
    qc.invalidateQueries({ queryKey: ['taskEvents'] });
  },
});

const fanficProgressCfg = (
  qc: QueryClient
): Cfg<{ success: boolean }, FanficProgressVars> => ({
  ...ONLINE,
  mutationFn: vars => api.fanfic.saveProgress(vars.ficId, vars.chapterId),
  onSettled: (_d, _e, vars) => {
    qc.invalidateQueries({ queryKey: ['fanfic', 'fic', vars.ficId] });
    qc.invalidateQueries({ queryKey: ['fanfic', 'chapters', vars.ficId] });
  },
});

const fanficSetReadCfg = (
  qc: QueryClient
): Cfg<{ success: boolean }, FanficSetReadVars> => ({
  ...ONLINE,
  mutationFn: vars => api.fanfic.setRead(vars.ficId, vars.ids, vars.read),
  onSettled: () => qc.invalidateQueries({ queryKey: ['fanfic'] }),
});

const writingChapterUpdateCfg = (
  qc: QueryClient
): Cfg<{ success: boolean }, WritingChapterUpdateVars> => ({
  ...ONLINE,
  mutationFn: vars =>
    api.writing.updateChapter(vars.chapterId, {
      title: vars.title,
      content: vars.content,
    }),
  onSettled: () => qc.invalidateQueries({ queryKey: ['writing'] }),
});

const writingNoteUpdateCfg = (
  qc: QueryClient
): Cfg<{ success: boolean }, WritingNoteUpdateVars> => ({
  ...ONLINE,
  mutationFn: vars =>
    api.writing.updateNote(vars.noteId, {
      title: vars.title,
      content: vars.content,
      docType: vars.docType,
    }),
  onSettled: () => qc.invalidateQueries({ queryKey: ['writing'] }),
});

const learningAttemptCfg = (
  qc: QueryClient
): Cfg<{ success: boolean; id: string }, LearningAttemptVars> => ({
  ...ONLINE,
  mutationFn: vars =>
    api.learning.saveAttempt({
      id: vars.id,
      cardId: vars.cardId,
      mode: vars.mode,
      answer: vars.answer,
      answerMode: vars.answerMode,
      speechMode: vars.speechMode,
    }),
  onSettled: () => qc.invalidateQueries({ queryKey: ['learning', 'attempts'] }),
});

const learningReviewCfg = (
  qc: QueryClient
): Cfg<{ due: string; state: string }, LearningReviewVars> => ({
  ...ONLINE,
  mutationFn: vars =>
    api.learning.review(vars.cardId, {
      rating: vars.rating,
      suggestedRating: vars.suggestedRating,
      userAnswer: vars.userAnswer,
      coverage: vars.coverage,
      answerMode: vars.answerMode,
      reviewId: vars.reviewId,
    }),
  onSettled: () => qc.invalidateQueries({ queryKey: ['learning', 'stats'] }),
});

const notebookWriteCfg = (
  qc: QueryClient
): Cfg<{ success: boolean }, NotebookWriteVars> => ({
  ...ONLINE,
  mutationFn: vars => api.notebook.files.write(vars.path, vars.content),
  onSettled: () => qc.invalidateQueries({ queryKey: ['notebook'] }),
});

/**
 * Register every offline-queueable mutation's default behavior under its key,
 * so a mutation paused before a page reload can be replayed by
 * `resumePausedMutations()`. Call once, before render, in main.tsx.
 */
export function registerOfflineMutationDefaults(qc: QueryClient): void {
  const pairs: Array<[readonly unknown[], Cfg<unknown, never>]> = [
    [MUTATION_KEYS.foodCreate, foodCreateCfg(qc) as Cfg<unknown, never>],
    [MUTATION_KEYS.paperCreate, paperCreateCfg(qc) as Cfg<unknown, never>],
    [MUTATION_KEYS.paperPageAdd, paperPageAddCfg(qc) as Cfg<unknown, never>],
    [MUTATION_KEYS.paperPageSave, paperPageSaveCfg(qc) as Cfg<unknown, never>],
    [MUTATION_KEYS.paperImageAdd, paperImageAddCfg(qc) as Cfg<unknown, never>],
    [
      MUTATION_KEYS.paperImageUpdate,
      paperImageUpdateCfg(qc) as Cfg<unknown, never>,
    ],
    [MUTATION_KEYS.recipeCreate, recipeCreateCfg(qc) as Cfg<unknown, never>],
    [MUTATION_KEYS.selfieUpload, selfieUploadCfg(qc) as Cfg<unknown, never>],
    [
      MUTATION_KEYS.calendarCreate,
      calendarCreateCfg(qc) as Cfg<unknown, never>,
    ],
    [MUTATION_KEYS.ideaCreate, ideaCreateCfg(qc) as Cfg<unknown, never>],
    [MUTATION_KEYS.calorieLog, calorieLogCfg(qc) as Cfg<unknown, never>],
    [MUTATION_KEYS.journalCreate, journalCreateCfg(qc) as Cfg<unknown, never>],
    [MUTATION_KEYS.journalUpdate, journalUpdateCfg(qc) as Cfg<unknown, never>],
    [
      MUTATION_KEYS.journalRecording,
      journalRecordingCfg(qc) as Cfg<unknown, never>,
    ],
    [MUTATION_KEYS.todoCreate, todoCreateCfg(qc) as Cfg<unknown, never>],
    [MUTATION_KEYS.todoUpdate, todoUpdateCfg(qc) as Cfg<unknown, never>],
    [MUTATION_KEYS.dailyToggle, dailyToggleCfg(qc) as Cfg<unknown, never>],
    [
      MUTATION_KEYS.fanficProgress,
      fanficProgressCfg(qc) as Cfg<unknown, never>,
    ],
    [MUTATION_KEYS.fanficSetRead, fanficSetReadCfg(qc) as Cfg<unknown, never>],
    [
      MUTATION_KEYS.writingChapterUpdate,
      writingChapterUpdateCfg(qc) as Cfg<unknown, never>,
    ],
    [
      MUTATION_KEYS.writingNoteUpdate,
      writingNoteUpdateCfg(qc) as Cfg<unknown, never>,
    ],
    [
      MUTATION_KEYS.learningAttempt,
      learningAttemptCfg(qc) as Cfg<unknown, never>,
    ],
    [
      MUTATION_KEYS.learningReview,
      learningReviewCfg(qc) as Cfg<unknown, never>,
    ],
    [MUTATION_KEYS.notebookWrite, notebookWriteCfg(qc) as Cfg<unknown, never>],
  ];
  for (const [key, cfg] of pairs) qc.setMutationDefaults(key, cfg);
}

// --- typed hooks: apply the shared config inline (works standalone) and let
// the caller layer on UI-only callbacks. ---

// Callers layer on UI-only concerns; everything behavioral belongs to the Cfg,
// so that a mounted component and a headless replay behave identically.
type CallerOptions<TData, TVars> = Omit<
  UseMutationOptions<TData, Error, TVars>,
  | 'mutationFn'
  | 'mutationKey'
  | 'onMutate'
  | 'onSettled'
  | 'networkMode'
  | 'retry'
  | 'retryDelay'
>;

function useOfflineMutation<TData, TVars>(
  mutationKey: readonly unknown[],
  cfg: (qc: QueryClient) => Cfg<TData, TVars>,
  options?: CallerOptions<TData, TVars>
) {
  const qc = useQueryClient();
  return useMutation<TData, Error, TVars>({
    mutationKey,
    ...cfg(qc),
    ...options,
  });
}

const calendarCreateCfg = (
  qc: QueryClient
): Cfg<{ id: string }, CalendarCreateVars> => ({
  ...ONLINE,
  mutationFn: vars => api.calendar.create(vars),
  // No optimistic patch: the calendar's cache is keyed by the visible range and
  // recurrence is expanded server-side, so a plausible-looking local occurrence
  // is exactly the kind of half-truth that would then disagree with the real
  // one. The event is queued and appears when the range refetches.
  onSettled: () => qc.invalidateQueries({ queryKey: ['calendar'] }),
});

const ideaCreateCfg = (
  qc: QueryClient
): Cfg<{ id: string }, IdeaCreateVars> => ({
  ...ONLINE,
  // The voice endpoint, not the plain create: it is what the capture box has
  // always used, and it runs the background polish pass that fixes a misheard
  // name against the memory document. Queuing must not quietly downgrade that.
  mutationFn: vars =>
    vars.rawContent !== undefined
      ? api.ideas.createFromVoice(vars.rawContent, vars.id, vars.repoId)
      : api.ideas.create(vars),
  onMutate: vars => {
    insertPendingIdea(qc, vars.id, vars.repoId, {
      title: vars.title || (vars.rawContent ?? '').slice(0, 80),
      tags: vars.tags,
    });
  },
  onSettled: () => qc.invalidateQueries({ queryKey: ['ideas'] }),
});

/**
 * Put an idea in the list before the server has one.
 *
 * Shared by the two ways an idea is captured — typed/dictated text, and a
 * recording that has not been transcribed yet — because both need the row on
 * screen immediately and neither can wait for a round trip. A recording's row
 * arrives with no title at all, which is honest: `displayTitle` shows
 * "Untitled idea" until the transcript lands, and the detail pane says it is
 * transcribing.
 */
function insertPendingIdea(
  qc: QueryClient,
  id: string,
  repoId?: string,
  opts: { title?: string; tags?: string[] } = {}
): void {
  const nowIso = new Date().toISOString();
  const captured: IdeaSummary = {
    id,
    title: opts.title ?? '',
    status: 'new',
    tags: opts.tags ? JSON.stringify(opts.tags) : null,
    sketchCount: 0,
    openQuestionCount: 0,
    articleCount: 0,
    hasPlan: false,
    verdict: null,
    confidence: null,
    effort: null,
    onRoadmap: false,
    assessmentStale: false,
    userVerdict: null,
    researchState: null,
    // Null when the server will pick the default: the optimistic row cannot
    // know which repo that is, and guessing would make the row jump between
    // filters when the real one arrives.
    repoId: repoId ?? null,
    createdAt: nowIso,
    updatedAt: nowIso,
  };
  qc.setQueryData<IdeaSummary[]>(['ideas'], old => {
    if (!old) return [captured];
    // Already on screen — a replayed upload after a reload, most likely, and
    // the row it finds may have a title and text on it by now. Blanking that
    // back to an empty capture until the next refetch would be a lie about
    // what the server holds.
    if (old.some(i => i.id === id)) return old;
    return [captured, ...old];
  });
}

const calorieLogCfg = (qc: QueryClient): Cfg<CalorieLog, CalorieLogVars> => ({
  ...ONLINE,
  mutationFn: vars => api.lifestyle.calories.create(vars),
  onMutate: vars => {
    const entry: CalorieLog = {
      id: vars.id,
      date: vars.date ?? new Date().toISOString().slice(0, 10),
      description: vars.description,
      calories: vars.calories,
      createdAt: new Date().toISOString(),
    };
    qc.setQueriesData<CalorieDay>(
      {
        predicate: q =>
          q.queryKey[0] === 'lifestyle' && q.queryKey[1] === 'calories',
      },
      old =>
        old && old.date === entry.date
          ? {
              ...old,
              entries: [...old.entries, entry],
              total: old.total + entry.calories,
            }
          : old
    );
  },
  onSettled: () =>
    qc.invalidateQueries({ queryKey: ['lifestyle', 'calories'] }),
});

const foodCreateCfg = (qc: QueryClient): Cfg<FoodEntry, FoodCreateVars> => ({
  ...ONLINE,
  mutationFn: async vars => {
    // The photos come back off the device, not out of the mutation's payload —
    // and they are loaded at *replay* time, which is the point: a meal captured
    // in a basement uploads its picture days later from the same blob it stored
    // the moment it was taken.
    const stored = await Promise.all(vars.photoIds.map(id => getPhoto(id)));
    const present = vars.photoIds.filter((_, i) => stored[i]);
    try {
      const entry = await api.food.create({
        id: vars.id,
        text: vars.text,
        latitude: vars.latitude,
        longitude: vars.longitude,
        media: stored.flatMap(p => (p ? [p.blob] : [])),
        mediaIds: present,
      });
      // Confirmed stored. This is the only place the photos may be let go of.
      await Promise.all(present.map(id => deletePhoto(id)));
      return entry;
    } catch (e) {
      // A 4xx is the server refusing this upload — retrying changes nothing —
      // but the photo is kept either way. Marking happens here rather than in
      // an `onError` so it also runs for a headless replay after a reload.
      const message = e instanceof Error ? e.message : 'Upload failed';
      await Promise.all(
        present.map(id => markPhotoAttempt(id, message, isTerminal(e)))
      );
      throw e;
    }
  },
  onSettled: () => qc.invalidateQueries({ queryKey: ['food'] }),
});

const selfieUploadCfg = (qc: QueryClient): Cfg<Selfie, SelfieUploadVars> => ({
  ...ONLINE,
  mutationFn: async vars => {
    const stored = await getPhoto(vars.photoId);
    if (!stored) throw new Error('That photo is no longer on this device.');
    try {
      const selfie = await api.lifestyle.selfies.upload(
        stored.blob,
        vars.date,
        stored.meta.filename
      );
      await deletePhoto(vars.photoId);
      return selfie;
    } catch (e) {
      await markPhotoAttempt(
        vars.photoId,
        e instanceof Error ? e.message : 'Upload failed',
        isTerminal(e)
      );
      throw e;
    }
  },
  // No client id needed here, unlike every other queued create: the route is
  // already idempotent by day — one selfie per date, a re-upload replaces it —
  // so a replay overwrites itself rather than leaving two.
  onSettled: () => qc.invalidateQueries({ queryKey: ['lifestyle', 'selfies'] }),
});

const paperCreateCfg = (
  qc: QueryClient
): Cfg<{ id: string }, PaperCreateVars> => ({
  ...ONLINE,
  ...PAPER_LANE,
  mutationFn: vars => api.paper.create(vars),
  onMutate: vars => {
    // Seed the detail the editor is about to open. Without this a paper started
    // offline navigates to a page whose query is paused and whose cache is
    // empty — a blank editor for a paper that does exist, on this device, with
    // ids the server will agree with later.
    qc.setQueryData<PaperDetail>(['paper', vars.id], {
      id: vars.id,
      title: '',
      archiveRequested: false,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      pages: [{ id: vars.pageId, position: 0, imageUrl: null }],
    });
  },
  onSettled: () => qc.invalidateQueries({ queryKey: ['paper'], exact: true }),
});

const paperPageAddCfg = (
  qc: QueryClient
): Cfg<{ id: string }, PaperPageAddVars> => ({
  ...ONLINE,
  ...PAPER_LANE,
  mutationFn: vars => api.paper.addPage(vars.paperId, vars.pageId),
  onMutate: vars => {
    // The page has to exist on the tablet the moment it is asked for — that is
    // the whole point of a fresh page. The server will compute its own
    // position on replay; this one only has to be right for the client's
    // ordering until then.
    qc.setQueryData<PaperDetail>(['paper', vars.paperId], prev =>
      prev
        ? {
            ...prev,
            pages: [
              ...prev.pages,
              {
                id: vars.pageId,
                position: prev.pages.length,
                imageUrl: null,
              },
            ],
          }
        : prev
    );
  },
  onSettled: () => qc.invalidateQueries({ queryKey: ['paper'], exact: true }),
});

const paperPageSaveCfg = (
  qc: QueryClient
): Cfg<{ success: boolean }, PaperPageSaveVars> => ({
  ...ONLINE,
  ...PAPER_LANE,
  mutationFn: async vars => {
    const pending = await getPageSave(vars.pageId);
    // Nothing pending means an earlier attempt already landed it. Not an
    // error: a page save is the whole page, so there is nothing left to send.
    if (!pending) return { success: true };
    const result = await api.paper.savePage(vars.pageId, {
      strokes: pending.meta.strokes,
      width: pending.meta.width,
      height: pending.meta.height,
      snapshot: pending.snapshot,
    });
    await clearPageSave(vars.pageId, pending.meta.revision);
    return result;
  },
  onSettled: () => qc.invalidateQueries({ queryKey: ['paper'], exact: true }),
});

const paperImageAddCfg = (
  qc: QueryClient
): Cfg<PaperPageImage, PaperImageAddVars> => ({
  ...ONLINE,
  ...PAPER_LANE,
  mutationFn: async vars => {
    const stored = await getPhoto(vars.imageId);
    if (!stored) throw new Error('That picture is no longer on this device.');
    try {
      const image = await api.paper.addImage(
        vars.pageId,
        stored.blob,
        vars.box,
        vars.filename,
        vars.imageId
      );
      await deletePhoto(vars.imageId);
      return image;
    } catch (e) {
      await markPhotoAttempt(
        vars.imageId,
        e instanceof Error ? e.message : 'Upload failed',
        isTerminal(e)
      );
      throw e;
    }
  },
  onMutate: vars => {
    // The picture has to be *on the page* the instant it is pasted, backend or
    // no backend. `url` is left empty on purpose: a blob: URL baked into the
    // persisted cache is dead after a reload, so the editor resolves the image
    // from the device store instead (see PaperEditor's localUrls).
    qc.setQueryData<PaperPageContent>(['paper', 'page', vars.pageId], prev =>
      prev
        ? {
            ...prev,
            images: [
              ...prev.images,
              {
                id: vars.imageId,
                pageId: vars.pageId,
                url: '',
                ...vars.box,
                rotation: 0,
                flipped: 0,
                locked: 0,
                position: prev.images.length,
              },
            ],
          }
        : prev
    );
  },
  onSuccess: (image, vars) => {
    // Swap the placeholder for the row the server actually stored — above all
    // for its `url`. The device copy is deleted the moment the upload lands, so
    // a cache entry still carrying the empty placeholder url has nothing left
    // to draw from: the picture would blank out on the next visit to the page,
    // looking exactly like the photo was lost.
    qc.setQueryData<PaperPageContent>(['paper', 'page', vars.pageId], prev =>
      prev
        ? {
            ...prev,
            images: prev.images.some(i => i.id === image.id)
              ? prev.images.map(i => (i.id === image.id ? image : i))
              : [...prev.images, image],
          }
        : prev
    );
  },
});

const paperImageUpdateCfg = (
  qc: QueryClient
): Cfg<PaperPageImage, PaperImageUpdateVars> => ({
  ...ONLINE,
  ...PAPER_LANE,
  // A PATCH of the geometry is last-write-wins on its own, so a replay is
  // harmless and no client id is needed beyond the image's own.
  mutationFn: vars => api.paper.updateImage(vars.imageId, vars.edit),
  onMutate: vars => {
    qc.setQueryData<PaperPageContent>(['paper', 'page', vars.pageId], prev =>
      prev
        ? {
            ...prev,
            images: prev.images.map(i =>
              i.id === vars.imageId
                ? {
                    ...i,
                    ...vars.edit,
                    flipped:
                      vars.edit.flipped === undefined
                        ? i.flipped
                        : vars.edit.flipped
                          ? 1
                          : 0,
                    locked:
                      vars.edit.locked === undefined
                        ? i.locked
                        : vars.edit.locked
                          ? 1
                          : 0,
                  }
                : i
            ),
          }
        : prev
    );
  },
});

const recipeCreateCfg = (qc: QueryClient): Cfg<Recipe, RecipeCreateVars> => ({
  ...ONLINE,
  mutationFn: async vars => {
    const stored = await Promise.all(vars.photoIds.map(id => getPhoto(id)));
    const present = vars.photoIds.filter((_, i) => stored[i]);
    try {
      const recipe = await api.cookbook.create({
        id: vars.id,
        title: vars.title,
        content: vars.content,
        tags: vars.tags,
        media: stored.flatMap(p => (p ? [p.blob] : [])),
        mediaIds: present,
      });
      await Promise.all(present.map(id => deletePhoto(id)));
      return recipe;
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Upload failed';
      await Promise.all(
        present.map(id => markPhotoAttempt(id, message, isTerminal(e)))
      );
      throw e;
    }
  },
  onSettled: () => qc.invalidateQueries({ queryKey: ['recipes'] }),
});

export const usePaperCreate = (
  o?: CallerOptions<{ id: string }, PaperCreateVars>
) => useOfflineMutation(MUTATION_KEYS.paperCreate, paperCreateCfg, o);

export const usePaperPageAdd = (
  o?: CallerOptions<{ id: string }, PaperPageAddVars>
) => useOfflineMutation(MUTATION_KEYS.paperPageAdd, paperPageAddCfg, o);

export const usePaperPageSave = (
  o?: CallerOptions<{ success: boolean }, PaperPageSaveVars>
) => useOfflineMutation(MUTATION_KEYS.paperPageSave, paperPageSaveCfg, o);

export const usePaperImageAdd = (
  o?: CallerOptions<PaperPageImage, PaperImageAddVars>
) => useOfflineMutation(MUTATION_KEYS.paperImageAdd, paperImageAddCfg, o);

export const usePaperImageUpdate = (
  o?: CallerOptions<PaperPageImage, PaperImageUpdateVars>
) => useOfflineMutation(MUTATION_KEYS.paperImageUpdate, paperImageUpdateCfg, o);

export const useRecipeCreate = (o?: CallerOptions<Recipe, RecipeCreateVars>) =>
  useOfflineMutation(MUTATION_KEYS.recipeCreate, recipeCreateCfg, o);

export const useFoodCreate = (o?: CallerOptions<FoodEntry, FoodCreateVars>) =>
  useOfflineMutation(MUTATION_KEYS.foodCreate, foodCreateCfg, o);

export const useSelfieUpload = (o?: CallerOptions<Selfie, SelfieUploadVars>) =>
  useOfflineMutation(MUTATION_KEYS.selfieUpload, selfieUploadCfg, o);

export const useCalendarCreate = (
  o?: CallerOptions<{ id: string }, CalendarCreateVars>
) => useOfflineMutation(MUTATION_KEYS.calendarCreate, calendarCreateCfg, o);

export const useIdeaCreate = (
  o?: CallerOptions<{ id: string }, IdeaCreateVars>
) => useOfflineMutation(MUTATION_KEYS.ideaCreate, ideaCreateCfg, o);

export const useCalorieLog = (o?: CallerOptions<CalorieLog, CalorieLogVars>) =>
  useOfflineMutation(MUTATION_KEYS.calorieLog, calorieLogCfg, o);

export const useJournalCreate = (
  o?: CallerOptions<{ id: string }, JournalCreateVars>
) => useOfflineMutation(MUTATION_KEYS.journalCreate, journalCreateCfg, o);

export const useJournalUpdate = (
  o?: CallerOptions<{ success: boolean }, JournalUpdateVars>
) => useOfflineMutation(MUTATION_KEYS.journalUpdate, journalUpdateCfg, o);

export const useJournalRecording = (
  o?: CallerOptions<RecordingResult, JournalRecordingVars>
) => useOfflineMutation(MUTATION_KEYS.journalRecording, journalRecordingCfg, o);

export const useTodoCreate = (
  o?: CallerOptions<{ id: string }, TodoCreateVars>
) => useOfflineMutation(MUTATION_KEYS.todoCreate, todoCreateCfg, o);

export const useTodoUpdate = (
  o?: CallerOptions<{ success: boolean }, TodoUpdateVars>
) => useOfflineMutation(MUTATION_KEYS.todoUpdate, todoUpdateCfg, o);

export const useDailyToggle = (
  o?: CallerOptions<{ success: boolean }, DailyToggleVars>
) => useOfflineMutation(MUTATION_KEYS.dailyToggle, dailyToggleCfg, o);

export const useFanficProgress = (
  o?: CallerOptions<{ success: boolean }, FanficProgressVars>
) => useOfflineMutation(MUTATION_KEYS.fanficProgress, fanficProgressCfg, o);

export const useFanficSetRead = (
  o?: CallerOptions<{ success: boolean }, FanficSetReadVars>
) => useOfflineMutation(MUTATION_KEYS.fanficSetRead, fanficSetReadCfg, o);

export const useWritingChapterUpdate = (
  o?: CallerOptions<{ success: boolean }, WritingChapterUpdateVars>
) =>
  useOfflineMutation(
    MUTATION_KEYS.writingChapterUpdate,
    writingChapterUpdateCfg,
    o
  );

export const useWritingNoteUpdate = (
  o?: CallerOptions<{ success: boolean }, WritingNoteUpdateVars>
) =>
  useOfflineMutation(MUTATION_KEYS.writingNoteUpdate, writingNoteUpdateCfg, o);

export const useLearningAttempt = (
  o?: CallerOptions<{ success: boolean; id: string }, LearningAttemptVars>
) => useOfflineMutation(MUTATION_KEYS.learningAttempt, learningAttemptCfg, o);

export const useLearningReview = (
  o?: CallerOptions<{ due: string; state: string }, LearningReviewVars>
) => useOfflineMutation(MUTATION_KEYS.learningReview, learningReviewCfg, o);

export const useNotebookWrite = (
  o?: CallerOptions<{ success: boolean }, NotebookWriteVars>
) => useOfflineMutation(MUTATION_KEYS.notebookWrite, notebookWriteCfg, o);
