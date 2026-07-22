import {
  useMutation,
  useQueryClient,
  type QueryClient,
  type UseMutationOptions,
} from '@tanstack/react-query';
import {
  api,
  type ClaimCoverage,
  type DailyTask,
  type JournalEntry,
  type TodoItem,
  type TodoPayload,
} from '../hooks/api';

/**
 * Offline write queue.
 *
 * These mutations use `networkMode: 'online'`, so while the backend is
 * unreachable react-query *pauses* them (instead of erroring) and replays them
 * via `resumePausedMutations()` on reconnect. Everything NOT listed here keeps
 * react-query's global `networkMode: 'offlineFirst'` default (set in main.tsx),
 * which fails fast offline — that's deliberately how deletes, AI calls and heavy
 * pipelines stay online-only with no per-call work.
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
  todoCreate: ['todos', 'create'] as const,
  todoUpdate: ['todos', 'update'] as const,
  dailyToggle: ['tasks', 'toggle'] as const,
  fanficProgress: ['fanfic', 'progress'] as const,
  fanficSetRead: ['fanfic', 'setRead'] as const,
  writingChapterUpdate: ['writing', 'chapter', 'update'] as const,
  writingNoteUpdate: ['writing', 'note', 'update'] as const,
  learningReview: ['learning', 'review'] as const,
  notebookWrite: ['notebook', 'write'] as const,
};

// --- variable shapes (self-contained so a reloaded replay can run them) ---

export interface JournalCreateVars {
  id: string;
  content: string;
  title?: string;
  tags?: string[];
}
export interface JournalUpdateVars {
  id: string;
  content: string;
  title: string;
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

// The behavioral slice of a mutation's options that both the hook and the
// registered default share.
type Cfg<TData, TVars> = Pick<
  UseMutationOptions<TData, Error, TVars>,
  'networkMode' | 'mutationFn' | 'onMutate' | 'onSettled'
>;

const ONLINE = { networkMode: 'online' as const };

// --- optimistic cache updaters (only for cache-driven UIs whose shapes we
// know; queue-only mutations reconcile on reconnect via onSettled) ---

function patchJournalLists(
  qc: QueryClient,
  fn: (list: JournalEntry[]) => JournalEntry[]
) {
  qc.setQueriesData<JournalEntry[]>(
    {
      predicate: q =>
        q.queryKey[0] === 'journal' && typeof q.queryKey[1] === 'object',
    },
    old => (old ? fn(old) : old)
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
    patchJournalLists(qc, list => [entry, ...list]);
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
  onSettled: () => qc.invalidateQueries({ queryKey: ['todos'] }),
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
  onSettled: () => qc.invalidateQueries({ queryKey: ['tasks'] }),
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
    [MUTATION_KEYS.journalCreate, journalCreateCfg(qc) as Cfg<unknown, never>],
    [MUTATION_KEYS.journalUpdate, journalUpdateCfg(qc) as Cfg<unknown, never>],
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
      MUTATION_KEYS.learningReview,
      learningReviewCfg(qc) as Cfg<unknown, never>,
    ],
    [MUTATION_KEYS.notebookWrite, notebookWriteCfg(qc) as Cfg<unknown, never>],
  ];
  for (const [key, cfg] of pairs) qc.setMutationDefaults(key, cfg);
}

// --- typed hooks: apply the shared config inline (works standalone) and let
// the caller layer on UI-only callbacks. ---

type CallerOptions<TData, TVars> = Omit<
  UseMutationOptions<TData, Error, TVars>,
  'mutationFn' | 'mutationKey' | 'onMutate' | 'onSettled' | 'networkMode'
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

export const useJournalCreate = (
  o?: CallerOptions<{ id: string }, JournalCreateVars>
) => useOfflineMutation(MUTATION_KEYS.journalCreate, journalCreateCfg, o);

export const useJournalUpdate = (
  o?: CallerOptions<{ success: boolean }, JournalUpdateVars>
) => useOfflineMutation(MUTATION_KEYS.journalUpdate, journalUpdateCfg, o);

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

export const useLearningReview = (
  o?: CallerOptions<{ due: string; state: string }, LearningReviewVars>
) => useOfflineMutation(MUTATION_KEYS.learningReview, learningReviewCfg, o);

export const useNotebookWrite = (
  o?: CallerOptions<{ success: boolean }, NotebookWriteVars>
) => useOfflineMutation(MUTATION_KEYS.notebookWrite, notebookWriteCfg, o);
