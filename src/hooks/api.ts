// Typed API client — replaces tRPC hooks

export interface JournalEntry {
  id: string;
  content: string;
  rawContent: string | null;
  title: string | null;
  tags: string | null;
  curatedTags: string[];
  ficRefs?: FicRef[];
  createdAt: string;
  updatedAt: string;
}

export interface FicRef {
  ficId: string;
  ficTitle: string;
  chapterId: string | null;
  chapterTitle: string | null;
}

export interface FicDownloadProgress {
  phase: 'index' | 'chapters' | 'updating' | 'done' | 'error';
  chaptersDone: number;
  chaptersTotal: number | null;
  error: string | null;
  done: boolean;
}

export interface Fic {
  id: string;
  title: string;
  author: string | null;
  sourceType: 'xenforo' | 'epub' | 'docx' | 'pdf';
  sourceUrl: string | null;
  site: string | null;
  description?: string | null;
  coverPath: string | null;
  wordCount: number;
  chapterCount: number;
  downloadStatus: 'downloading' | 'complete' | 'error';
  downloadError: string | null;
  updatePending?: boolean;
  lastReadChapterId: string | null;
  lastCheckedAt: string | null;
  rating: number | null;
  review?: string | null;
  createdAt: string;
  updatedAt: string;
  downloadProgress?: FicDownloadProgress;
  folderIds?: string[];
  tags?: string[];
  readCount?: number;
}

export interface FicFolder {
  id: string;
  name: string;
  position: number;
  ficCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface FicTagCount {
  name: string;
  count: number;
}

export interface RefreshAlertsResult {
  flagged: number;
  newImports: number;
  skippedFresh: number;
  skippedActive: number;
  alertsSeen: number;
  errors: Record<string, string>;
}

export interface FicChapterSummary {
  id: string;
  ficId: string;
  position: number;
  title: string;
  category: string;
  wordCount: number;
  postedAt: string | null;
  isRead: boolean;
}

export interface FicChapter extends FicChapterSummary {
  contentHtml: string;
  contentText: string;
  sourceUrl: string | null;
  createdAt: string;
}

export interface SiteCookieInfo {
  domain: string;
  hasCookie: boolean;
  updatedAt: string | null;
}

export interface Transcription {
  id: string;
  text: string;
  source: string;
  app: string | null;
  detail: string | null;
  createdAt: string;
}

export interface MeetingSegment {
  start: number;
  end: number;
  speaker: string;
  text: string;
}

export type MeetingStatus = 'recording' | 'transcribing' | 'done' | 'error';
export type MeetingPhase =
  | 'recording'
  | 'awaiting_start'
  | 'transcribing_mic'
  | 'transcribing_system'
  | 'paused_mic'
  | 'paused_system'
  | 'diarizing'
  | 'summarizing'
  | 'done'
  | 'error';

export interface Meeting {
  id: string;
  title: string | null;
  status: MeetingStatus;
  phase: MeetingPhase;
  error: string | null;
  durationSeconds: number | null;
  hasNotes: boolean;
  hasSummary: boolean;
  startedAt: string;
  endedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface MeetingDetail extends Omit<
  Meeting,
  'hasNotes' | 'hasSummary'
> {
  source: 'live' | 'upload';
  whisperModel: string;
  whisperDevice: string;
  segments: MeetingSegment[] | null;
  transcriptText: string | null;
  speakerNames: Record<string, string> | null;
  summary: string | null;
  notes: string;
}

export interface ActiveMeeting {
  id: string | null;
  startedAt: string | null;
}

export interface Recipe {
  id: string;
  title: string;
  content: string;
  tags: string | null;
  sourceUrl: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface RecipeTag {
  name: string;
  count: number;
}

export interface CuratedTag {
  id: string;
  name: string;
  createdAt: string;
  entryCount: number;
  scanProgress?: { total: number; processed: number; done: boolean };
}

export interface CalendarEvent {
  id: string;
  title: string;
  description: string | null;
  date: string;
  time: string | null;
  endTime: string | null;
  tags: string | null;
  journalId: string | null;
  createdAt: string;
  linkedJournals?: JournalEntry[];
}

export interface LearningCard {
  id: string;
  folderId: string | null;
  question: string;
  answer: string;
  state: 'pending' | 'active' | 'retired';
  tags: string[];
  sourceType: string | null;
  sourceId: string | null;
  derivedFrom: string | null;
  revisedFrom: string | null;
  due: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface LearningStats {
  total: number;
  due: number;
  pending: number;
  mastered: number;
  learning: number;
}

export interface LearningTag {
  name: string;
  count: number;
}

export interface LearningFolder {
  id: string;
  name: string;
  position: number;
  evidenceProviderId: string | null;
  evidenceProviderName: string | null;
  activeCount: number;
  pendingCount: number;
  dueCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface McpServer {
  id: string;
  name: string;
  transport: 'stdio' | 'http';
  command: string | null;
  args: string[];
  env: Record<string, string>;
  url: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface CoverageClaim {
  text: string;
  essential: boolean;
  covered: boolean;
  note: string;
}

export interface ClaimCoverage {
  claims: CoverageClaim[];
  summary: string;
  gated?: boolean;
}

export interface GradeResult {
  coverage: ClaimCoverage;
  suggestedRating: number;
  normalizedAnswer: string;
}

export interface ApproveResult {
  status: 'approved' | 'duplicateHint';
  due?: string;
  similar?: { id: string; question: string; answer: string };
  score?: number;
}

export interface VerificationCitation {
  title: string;
  source: string;
  quote: string;
}

export interface VerificationCase {
  verdict: 'supports' | 'contradicts' | 'partial' | 'notFound';
  summary: string;
  proposedAnswer?: string;
  citations: VerificationCitation[];
}

export interface VerifyResult {
  status: 'ok' | 'notFound' | 'noProvider' | 'providerUnsupported';
  case: VerificationCase | null;
  transcript: unknown[];
  error?: string;
}

export interface CardChatResult {
  reply: string;
  transcript: unknown[];
  usedMcp: boolean;
}

export interface LearningRevision {
  id: string;
  oldCardId: string | null;
  newCardId: string;
  triggerType: 'manual_edit' | 'web_verification';
  oldAnswer: string;
  newAnswer: string;
  diff: string;
  isSemantic: boolean;
  sources: VerificationCitation[];
  note: string | null;
  createdAt: string;
}

export interface Conversation {
  id: string;
  title: string | null;
  writingProjectId?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface Message {
  id: string;
  conversationId: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  metadata: string | null;
  createdAt: string;
}

export interface ConversationWithMessages extends Conversation {
  messages: Message[];
}

export interface AppSettings {
  hasHfToken: boolean;
  ollamaUrl: string | null;
  ollamaModel: string | null;
  networkMode: boolean;
  networkCode: string | null;
  sttPasteKey: string | null;
  sttVoiceKey: string | null;
  sttJournalKey: string | null;
  sttCommandKey: string | null;
  sttBackend: string | null;
  ttsBackend: string | null;
  whisperModel: string | null;
  sttDevice: string | null;
  voicePipelineEnabled: boolean;
  preventSleep: boolean;
  meetingEchoCancel: boolean;
  nudgeEnabled: boolean;
  nudgeIntervalMinutes: number;
}

export interface WhisperModel {
  name: string;
  vramMb: number;
}

export interface OllamaModel {
  name: string;
  vramMb: number;
}

export interface GpuVram {
  available: boolean;
  baseMb?: number;
  totalMb?: number;
}

export interface AuthStatus {
  authenticated: boolean;
  networkMode: boolean;
}

export interface FileEntry {
  name: string;
  path: string;
  isDir: boolean;
  size: number | null;
  modified: number;
}

export interface NotebookReviewState {
  path: string;
  enabled: boolean;
  fsrsState: string | null;
  due: string | null;
}

export interface RAGResult {
  sourceId: string;
  sourceType: string;
  title?: string;
  score: number;
  preview: string;
}

export interface RAGContext {
  context: string;
  results: RAGResult[];
  isConfigured: boolean;
}

export interface WritingProject {
  id: string;
  title: string;
  description: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface WritingChapterSummary {
  id: string;
  projectId: string;
  title: string;
  position: number;
  createdAt: string;
  updatedAt: string;
}

export interface WritingChapter extends WritingChapterSummary {
  content: string;
}

export interface WritingNoteSummary {
  id: string;
  projectId: string;
  title: string;
  docType: 'character' | 'outline' | 'worldbuilding' | 'note';
  createdAt: string;
  updatedAt: string;
}

export interface WritingNote extends WritingNoteSummary {
  content: string;
}

export interface DailyTask {
  id: string;
  title: string;
  position: number;
  done: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface TodoItem {
  id: string;
  title: string;
  done: boolean;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface FrontPage {
  paper: string;
  label: string;
  date: string;
  imageUrl: string | null;
}

export interface SyncResult {
  paper: string;
  status: 'downloaded' | 'already-saved' | 'error';
  error?: string;
}

// --- fetch helpers ---

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url, { credentials: 'include' });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${r.status}`);
  }
  return r.json();
}

async function send<T>(
  method: string,
  url: string,
  body?: unknown
): Promise<T> {
  const r = await fetch(url, {
    method,
    credentials: 'include',
    headers:
      body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    const b = await r.json().catch(() => ({}));
    throw new Error(b.error || `HTTP ${r.status}`);
  }
  return r.json();
}

const post = <T>(url: string, body?: unknown) => send<T>('POST', url, body);
const patch = <T>(url: string, body: unknown) => send<T>('PATCH', url, body);
const put = <T>(url: string, body: unknown) => send<T>('PUT', url, body);
const del = <T>(url: string) => send<T>('DELETE', url);

async function upload<T>(url: string, form: FormData): Promise<T> {
  const r = await fetch(url, {
    method: 'POST',
    credentials: 'include',
    body: form,
  });
  if (!r.ok) {
    const b = await r.json().catch(() => ({}));
    throw new Error(b.error || `HTTP ${r.status}`);
  }
  return r.json();
}

// --- API namespaces ---

export const api = {
  auth: {
    status: () => get<AuthStatus>('/api/auth/status'),
    login: (password: string, code: string) =>
      post<{ success: boolean }>('/api/auth/login', { password, code }),
    logout: () => post<{ success: boolean }>('/api/auth/logout'),
  },

  settings: {
    get: () => get<AppSettings | null>('/api/settings'),
    updateAI: (data: Partial<AppSettings & { hfToken?: string }>) =>
      patch<{ success: boolean }>('/api/settings/ai', data),
    updateShortcuts: (data: {
      sttPasteKey?: string;
      sttVoiceKey?: string;
      sttJournalKey?: string;
      sttCommandKey?: string;
    }) => patch<{ success: boolean }>('/api/settings/ai', data),
    regenerateCode: () =>
      post<{ networkCode: string }>('/api/settings/regenerate-code'),
    ollamaModels: () => get<OllamaModel[]>('/api/settings/ollama-models'),
    gpuVram: () => get<GpuVram>('/api/settings/gpu-vram'),
  },

  journal: {
    list: (params?: {
      limit?: number;
      offset?: number;
      curatedTagId?: string;
    }) => {
      const qp = new URLSearchParams();
      if (params?.limit !== undefined) qp.set('limit', String(params.limit));
      if (params?.offset !== undefined) qp.set('offset', String(params.offset));
      if (params?.curatedTagId) qp.set('curated_tag_id', params.curatedTagId);
      return get<JournalEntry[]>(`/api/journal?${qp}`);
    },
    search: (query: string, limit?: number) =>
      get<JournalEntry[]>(
        `/api/journal/search?query=${encodeURIComponent(query)}&limit=${limit ?? 50}`
      ),
    semanticSearch: (query: string, limit?: number) =>
      get<JournalEntry[]>(
        `/api/journal/semantic-search?query=${encodeURIComponent(query)}&limit=${limit ?? 5}`
      ),
    get: (id: string) => get<JournalEntry>(`/api/journal/${id}`),
    create: (data: { content: string; title?: string; tags?: string[] }) =>
      post<{ id: string }>('/api/journal', data),
    // Mirrors the STT_JOURNAL_KEY voice shortcut (stt/listener.py): save the
    // raw transcript immediately, polish it in the background.
    createFromVoice: (rawContent: string) =>
      post<{ id: string }>('/api/journal', { raw_content: rawContent }),
    update: (
      id: string,
      data: { content?: string; title?: string; tags?: string[] }
    ) => patch<{ success: boolean }>(`/api/journal/${id}`, data),
    delete: (id: string) => del<{ success: boolean }>(`/api/journal/${id}`),
    polish: (id: string) =>
      post<{ success: boolean; content: string }>(`/api/journal/${id}/polish`),
  },

  transcriptions: {
    list: (params?: { limit?: number; offset?: number }) => {
      const qp = new URLSearchParams();
      if (params?.limit !== undefined) qp.set('limit', String(params.limit));
      if (params?.offset !== undefined) qp.set('offset', String(params.offset));
      return get<Transcription[]>(`/api/transcriptions?${qp}`);
    },
    delete: (id: string) =>
      del<{ success: boolean }>(`/api/transcriptions/${id}`),
  },

  meetings: {
    start: () => post<{ id: string }>('/api/meetings/start'),
    upload: (file: File, title?: string) => {
      const form = new FormData();
      form.append('audio', file);
      if (title?.trim()) form.append('title', title.trim());
      return upload<{ id: string }>('/api/meetings/upload', form);
    },
    stop: (id: string) =>
      post<{ success: boolean }>(`/api/meetings/${id}/stop`),
    startTranscription: (
      id: string,
      opts: { whisperModel: string; device: string }
    ) =>
      post<{ success: boolean }>(
        `/api/meetings/${id}/start-transcription`,
        opts
      ),
    retry: (id: string, opts: { whisperModel: string; device: string }) =>
      post<{ success: boolean }>(`/api/meetings/${id}/retry`, opts),
    redo: (id: string, opts: { whisperModel: string; device: string }) =>
      post<{ success: boolean }>(`/api/meetings/${id}/redo`, opts),
    pause: (id: string) =>
      post<{ success: boolean }>(`/api/meetings/${id}/pause`),
    resume: (id: string) =>
      post<{ success: boolean }>(`/api/meetings/${id}/resume`),
    active: () => get<ActiveMeeting>('/api/meetings/active'),
    list: () => get<Meeting[]>('/api/meetings'),
    get: (id: string) => get<MeetingDetail>(`/api/meetings/${id}`),
    update: (
      id: string,
      data: {
        title?: string;
        notes?: string;
        speakerNames?: Record<string, string> | null;
      }
    ) => patch<{ success: boolean }>(`/api/meetings/${id}`, data),
    delete: (id: string) => del<{ success: boolean }>(`/api/meetings/${id}`),
    summarize: (id: string) =>
      post<{ summary: string }>(`/api/meetings/${id}/summarize`),
    audioUrl: (id: string, track: 'mic' | 'system') =>
      `/api/meetings/${id}/audio/${track}`,
  },

  cookbook: {
    list: (params?: { limit?: number; offset?: number; tag?: string }) => {
      const qp = new URLSearchParams();
      if (params?.limit !== undefined) qp.set('limit', String(params.limit));
      if (params?.offset !== undefined) qp.set('offset', String(params.offset));
      if (params?.tag) qp.set('tag', params.tag);
      return get<Recipe[]>(`/api/cookbook?${qp}`);
    },
    search: (query: string, limit?: number) =>
      get<Recipe[]>(
        `/api/cookbook/search?query=${encodeURIComponent(query)}&limit=${limit ?? 50}`
      ),
    tags: () => get<RecipeTag[]>('/api/cookbook/tags'),
    get: (id: string) => get<Recipe>(`/api/cookbook/${id}`),
    create: (data: { title: string; content: string; tags?: string[] }) =>
      post<{ id: string }>('/api/cookbook', data),
    update: (
      id: string,
      data: { title?: string; content?: string; tags?: string[] }
    ) => patch<{ success: boolean }>(`/api/cookbook/${id}`, data),
    delete: (id: string) => del<{ success: boolean }>(`/api/cookbook/${id}`),
    importRecipe: (data: { text?: string; url?: string }) =>
      post<{ id: string; recipe: Recipe }>('/api/cookbook/import', data),
  },

  fanfic: {
    list: (params?: {
      limit?: number;
      offset?: number;
      folderId?: string;
      tag?: string;
      sort?: 'recent';
    }) => {
      const qp = new URLSearchParams();
      if (params?.limit !== undefined) qp.set('limit', String(params.limit));
      if (params?.offset !== undefined) qp.set('offset', String(params.offset));
      if (params?.folderId) qp.set('folderId', params.folderId);
      if (params?.tag) qp.set('tag', params.tag);
      if (params?.sort) qp.set('sort', params.sort);
      return get<Fic[]>(`/api/fanfic?${qp}`);
    },
    tags: () => get<FicTagCount[]>('/api/fanfic/tags'),
    folders: {
      list: () => get<FicFolder[]>('/api/fanfic/folders'),
      create: (name: string) =>
        post<{ id: string }>('/api/fanfic/folders', { name }),
      rename: (id: string, name: string) =>
        patch<{ success: boolean }>(`/api/fanfic/folders/${id}`, { name }),
      reorder: (ids: string[]) =>
        put<{ success: boolean }>('/api/fanfic/folders/order', { ids }),
      delete: (id: string) =>
        del<{ success: boolean }>(`/api/fanfic/folders/${id}`),
    },
    addToFolder: (ficId: string, folderId: string) =>
      post<{ success: boolean }>(`/api/fanfic/${ficId}/folders`, { folderId }),
    removeFromFolder: (ficId: string, folderId: string) =>
      del<{ success: boolean }>(`/api/fanfic/${ficId}/folders/${folderId}`),
    setRead: (ficId: string, chapterIds: string[], read: boolean) =>
      post<{ success: boolean; readCount: number }>(
        `/api/fanfic/${ficId}/read`,
        { chapterIds, read }
      ),
    saveReview: (
      ficId: string,
      data: { rating?: number | null; review?: string | null }
    ) => patch<{ success: boolean }>(`/api/fanfic/${ficId}/review`, data),
    search: (query: string) =>
      get<Fic[]>(`/api/fanfic/search?query=${encodeURIComponent(query)}`),
    get: (id: string) => get<Fic>(`/api/fanfic/${id}`),
    delete: (id: string) => del<{ success: boolean }>(`/api/fanfic/${id}`),
    chapters: (ficId: string) =>
      get<FicChapterSummary[]>(`/api/fanfic/${ficId}/chapters`),
    chapter: (chapterId: string) =>
      get<FicChapter>(`/api/fanfic/chapters/${chapterId}`),
    importUrl: (url: string) =>
      post<{ id: string; alreadyExists?: boolean }>('/api/fanfic/import', {
        url,
      }),
    status: (ficId: string) =>
      get<FicDownloadProgress | { done: true }>(`/api/fanfic/${ficId}/status`),
    checkUpdates: (ficId: string) =>
      post<{ id: string; queued: boolean }>(
        `/api/fanfic/${ficId}/check-updates`
      ),
    refreshAlerts: () =>
      post<RefreshAlertsResult>('/api/fanfic/refresh-alerts'),
    uploadFile: (file: File) => {
      const form = new FormData();
      form.append('file', file);
      return upload<{ id: string; fic: Fic }>('/api/fanfic/upload', form);
    },
    saveProgress: (ficId: string, chapterId: string) =>
      post<{ success: boolean }>(`/api/fanfic/${ficId}/progress`, {
        chapterId,
      }),
    linkJournal: (ficId: string, journalEntryId: string, chapterId?: string) =>
      post<{ id: string }>(`/api/fanfic/${ficId}/journal-link`, {
        journalEntryId,
        chapterId,
      }),
    unlinkJournal: (
      ficId: string,
      journalEntryId: string,
      chapterId?: string
    ) =>
      del<{ success: boolean }>(
        `/api/fanfic/${ficId}/journal-link/${journalEntryId}${chapterId ? `?chapterId=${chapterId}` : ''}`
      ),
    cookies: {
      list: () => get<SiteCookieInfo[]>('/api/fanfic/cookies'),
      put: (domain: string, cookie: string) =>
        put<{ success: boolean }>('/api/fanfic/cookies', { domain, cookie }),
    },
  },

  calendar: {
    listByRange: (start: string, end: string) =>
      get<CalendarEvent[]>(`/api/calendar?start=${start}&end=${end}`),
    listByDate: (date: string) =>
      get<CalendarEvent[]>(`/api/calendar/date/${date}`),
    listByWeek: (date: string) =>
      get<CalendarEvent[]>(`/api/calendar/week/${date}`),
    get: (id: string) => get<CalendarEvent>(`/api/calendar/${id}`),
    findRelatedJournals: (date: string) =>
      get<JournalEntry[]>(`/api/calendar/related-journals/${date}`),
    create: (data: {
      title: string;
      date: string;
      description?: string;
      time?: string;
      endTime?: string;
      tags?: string[];
      journalId?: string;
    }) => post<{ id: string }>('/api/calendar', data),
    update: (id: string, data: Record<string, unknown>) =>
      patch<{ success: boolean }>(`/api/calendar/${id}`, data),
    delete: (id: string) => del<{ success: boolean }>(`/api/calendar/${id}`),
    linkJournal: (id: string, journalEntryId: string) =>
      post<{ id: string }>(`/api/calendar/${id}/link`, { journalEntryId }),
    unlinkJournal: (id: string, journalEntryId: string) =>
      del<{ success: boolean }>(`/api/calendar/${id}/link/${journalEntryId}`),
  },

  learning: {
    listCards: (params?: {
      limit?: number;
      offset?: number;
      tag?: string;
      folderId?: string;
      state?: string;
    }) => {
      const qp = new URLSearchParams();
      if (params?.limit !== undefined) qp.set('limit', String(params.limit));
      if (params?.offset !== undefined) qp.set('offset', String(params.offset));
      if (params?.tag) qp.set('tag', params.tag);
      if (params?.folderId) qp.set('folderId', params.folderId);
      if (params?.state) qp.set('state', params.state);
      return get<LearningCard[]>(`/api/learning/cards?${qp}`);
    },
    getCard: (id: string) => get<LearningCard>(`/api/learning/cards/${id}`),
    createCard: (data: {
      question: string;
      answer: string;
      folderId?: string;
      tags?: string[];
    }) => post<{ id: string }>('/api/learning/cards', data),
    updateCard: (
      id: string,
      data: {
        question?: string;
        answer?: string;
        tags?: string[];
        folderId?: string | null;
      }
    ) => patch<{ success: boolean }>(`/api/learning/cards/${id}`, data),
    deleteCard: (id: string) =>
      del<{ success: boolean }>(`/api/learning/cards/${id}`),
    getDue: (params?: { tag?: string; folderId?: string }) => {
      const qp = new URLSearchParams();
      if (params?.tag) qp.set('tag', params.tag);
      if (params?.folderId) qp.set('folderId', params.folderId);
      return get<LearningCard[]>(`/api/learning/due?${qp}`);
    },
    getStats: (params?: { tag?: string; folderId?: string }) => {
      const qp = new URLSearchParams();
      if (params?.tag) qp.set('tag', params.tag);
      if (params?.folderId) qp.set('folderId', params.folderId);
      return get<LearningStats>(`/api/learning/stats?${qp}`);
    },
    getTags: () => get<LearningTag[]>('/api/learning/tags'),

    grade: (
      id: string,
      data: { answer: string; answerMode: 'typed' | 'voice' }
    ) => post<GradeResult>(`/api/learning/cards/${id}/grade`, data),
    review: (
      id: string,
      data: {
        rating: number;
        suggestedRating?: number;
        userAnswer?: string;
        coverage?: ClaimCoverage;
        answerMode?: 'typed' | 'voice' | 'self';
      }
    ) =>
      post<{ due: string; state: string }>(
        `/api/learning/cards/${id}/review`,
        data
      ),

    generate: (data: {
      text: string;
      folderId?: string;
      tags?: string[];
      sourceType?: string;
      sourceId?: string;
      derivedFrom?: string;
      direction?: string;
    }) =>
      post<{ count: number; ids: string[] }>('/api/learning/generate', data),
    generateFromJournal: (journalId: string, folderId?: string) =>
      post<{ count: number; ids: string[] }>(
        '/api/learning/generate-from-journal',
        { journalId, folderId }
      ),
    generateForTopic: (topic: string, folderId?: string) =>
      post<{ count: number; ids: string[] }>(
        '/api/learning/generate-for-topic',
        { topic, folderId }
      ),

    listQueue: () => get<LearningCard[]>('/api/learning/queue'),
    approve: (id: string, force?: boolean) =>
      post<ApproveResult>(`/api/learning/queue/${id}/approve`, { force }),
    regenerate: (id: string, direction: string) =>
      post<{ count: number; ids: string[] }>(
        `/api/learning/queue/${id}/regenerate`,
        { direction }
      ),
    deny: (id: string) =>
      del<{ success: boolean }>(`/api/learning/queue/${id}`),

    chat: (
      id: string,
      data: {
        message: string;
        transcript?: unknown[];
        mcpServerId?: string | null;
        userAnswer?: string;
      }
    ) => post<CardChatResult>(`/api/learning/cards/${id}/chat`, data),

    verify: (id: string) =>
      post<VerifyResult>(`/api/learning/cards/${id}/verify`, {}),
    verifyFollowup: (id: string, question: string, transcript: unknown[]) =>
      post<VerifyResult>(`/api/learning/cards/${id}/verify/followup`, {
        question,
        transcript,
      }),
    revise: (
      id: string,
      data: {
        answer: string;
        question?: string;
        triggerType?: 'manual_edit' | 'web_verification';
        sources?: VerificationCitation[];
        note?: string;
      }
    ) =>
      post<{ newCardId: string; isSemantic: boolean }>(
        `/api/learning/cards/${id}/revise`,
        data
      ),
    getRevisions: (id: string) =>
      get<LearningRevision[]>(`/api/learning/cards/${id}/revisions`),

    listFolders: () => get<LearningFolder[]>('/api/learning/folders'),
    createFolder: (name: string) =>
      post<{ id: string }>('/api/learning/folders', { name }),
    updateFolder: (
      id: string,
      data: {
        name?: string;
        position?: number;
        evidenceProviderId?: string | null;
      }
    ) => patch<{ success: boolean }>(`/api/learning/folders/${id}`, data),
    deleteFolder: (id: string) =>
      del<{ success: boolean }>(`/api/learning/folders/${id}`),

    listMcpServers: () => get<McpServer[]>('/api/learning/mcp-servers'),
    createMcpServer: (data: {
      name: string;
      transport: 'stdio' | 'http';
      command?: string;
      args?: string[];
      env?: Record<string, string>;
      url?: string;
    }) => post<{ id: string }>('/api/learning/mcp-servers', data),
    updateMcpServer: (
      id: string,
      data: Partial<{
        name: string;
        transport: 'stdio' | 'http';
        command: string;
        args: string[];
        env: Record<string, string>;
        url: string;
      }>
    ) => patch<{ success: boolean }>(`/api/learning/mcp-servers/${id}`, data),
    deleteMcpServer: (id: string) =>
      del<{ success: boolean }>(`/api/learning/mcp-servers/${id}`),
    testMcpServer: (id: string) =>
      post<{ ok: boolean; tools: string[]; error?: string }>(
        `/api/learning/mcp-servers/${id}/test`
      ),
  },

  chat: {
    listConversations: () => get<Conversation[]>('/api/chat/conversations'),
    getConversation: (id: string) =>
      get<ConversationWithMessages | null>(`/api/chat/conversations/${id}`),
    createConversation: (data?: { title?: string }) =>
      post<{ id: string }>('/api/chat/conversations', data ?? {}),
    updateTitle: (id: string, title: string) =>
      patch<{ success: boolean }>(`/api/chat/conversations/${id}/title`, {
        title,
      }),
    deleteConversation: (id: string) =>
      del<{ success: boolean }>(`/api/chat/conversations/${id}`),
    addMessage: (
      id: string,
      data: { role: string; content: string; metadata?: string }
    ) => post<{ id: string }>(`/api/chat/conversations/${id}/messages`, data),
    classify: (message: string) =>
      post<{ intent: string; confidence: number; [key: string]: unknown }>(
        '/api/chat/classify',
        { message }
      ),
    saveJournal: (data: {
      conversationId: string;
      messageId?: string;
      title: string;
      content: string;
      tags: string[];
    }) => post<{ id: string }>('/api/chat/save-journal', data),
    saveCalendar: (data: {
      conversationId: string;
      messageId?: string;
      title: string;
      description: string;
      date: string;
      time?: string;
      tags: string[];
    }) => post<{ id: string }>('/api/chat/save-calendar', data),
    ragContext: (message: string, limit?: number) =>
      post<RAGContext>('/api/chat/rag-context', { message, limit: limit ?? 3 }),
  },

  files: {
    list: (path?: string) =>
      get<FileEntry[]>(
        `/api/files?${path ? `path=${encodeURIComponent(path)}` : ''}`
      ),
    read: (path: string) =>
      get<{ content: string }>(
        `/api/files/read?path=${encodeURIComponent(path)}`
      ),
    write: (path: string, content: string) =>
      post<{ success: boolean }>('/api/files/write', { path, content }),
    rename: (from: string, to: string) =>
      post<{ success: boolean }>('/api/files/rename', { from, to }),
    delete: (path: string) =>
      del<{ success: boolean }>(`/api/files?path=${encodeURIComponent(path)}`),
    mkdir: (path: string) =>
      post<{ success: boolean }>('/api/files/mkdir', { path }),
  },

  notebook: {
    files: {
      list: (path?: string) =>
        get<FileEntry[]>(
          `/api/notebook/files?${path ? `path=${encodeURIComponent(path)}` : ''}`
        ),
      read: (path: string) =>
        get<{ content: string }>(
          `/api/notebook/files/read?path=${encodeURIComponent(path)}`
        ),
      write: (path: string, content: string) =>
        post<{ success: boolean }>('/api/notebook/files/write', {
          path,
          content,
        }),
      rename: (from: string, to: string) =>
        post<{ success: boolean }>('/api/notebook/files/rename', {
          from,
          to,
        }),
      delete: (path: string) =>
        del<{ success: boolean }>(
          `/api/notebook/files?path=${encodeURIComponent(path)}`
        ),
      mkdir: (path: string) =>
        post<{ success: boolean }>('/api/notebook/files/mkdir', { path }),
    },
    review: {
      getState: (path: string) =>
        get<NotebookReviewState>(
          `/api/notebook/review/state?path=${encodeURIComponent(path)}`
        ),
      toggle: (path: string, enabled: boolean) =>
        post<{ success: boolean }>('/api/notebook/review/toggle', {
          path,
          enabled,
        }),
      due: () => get<NotebookReviewState[]>('/api/notebook/review/due'),
      rate: (path: string, rating: 1 | 2 | 3 | 4) =>
        post<{ due: string }>('/api/notebook/review/rate', {
          path,
          rating,
        }),
    },
  },

  shortcuts: {
    get: () =>
      get<{ version: number; bindings: Record<string, string> }>(
        '/api/shortcuts'
      ),
    put: (bindings: Record<string, string>) =>
      put<{ success: boolean }>('/api/shortcuts', { version: 1, bindings }),
  },

  stt: {
    health: () =>
      get<{
        stt_backend: string;
        stt_model: string;
        stt_ready: boolean;
        tts_backend: string;
        tts_ready: boolean;
      }>('/api/stt/health'),
    whisperModels: () => get<WhisperModel[]>('/api/stt/whisper-models'),
    reload: () => post<{ success: boolean }>('/api/stt/reload'),
    listenerState: () =>
      get<{ recording: boolean; transcribing: boolean; mode: string | null }>(
        '/api/stt/listener-state'
      ),
  },

  rag: {
    isConfigured: () => get<boolean>('/api/rag/configured'),
    getStats: () =>
      get<{
        totalJournals: number;
        indexedJournals: number;
        totalChunks: number;
        isConfigured: boolean;
      }>('/api/rag/stats'),
    syncJournal: (journalId: string) =>
      post<{ chunks: number }>(`/api/rag/sync/${journalId}`),
    syncAll: () =>
      post<{ synced: number; chunks: number }>('/api/rag/sync-all'),
    search: (query: string, limit?: number) =>
      get<RAGResult[]>(
        `/api/rag/search?query=${encodeURIComponent(query)}&limit=${limit ?? 5}`
      ),
  },

  writing: {
    listProjects: () => get<WritingProject[]>('/api/writing/projects'),
    createProject: (data: { title: string; description?: string }) =>
      post<{ id: string }>('/api/writing/projects', data),
    getProject: (id: string) =>
      get<WritingProject>(`/api/writing/projects/${id}`),
    updateProject: (
      id: string,
      data: { title?: string; description?: string }
    ) => patch<{ success: boolean }>(`/api/writing/projects/${id}`, data),
    deleteProject: (id: string) =>
      del<{ success: boolean }>(`/api/writing/projects/${id}`),

    listChapters: (projectId: string) =>
      get<WritingChapterSummary[]>(
        `/api/writing/projects/${projectId}/chapters`
      ),
    createChapter: (projectId: string, data: { title: string }) =>
      post<{ id: string }>(`/api/writing/projects/${projectId}/chapters`, data),
    getChapter: (chapterId: string) =>
      get<WritingChapter>(`/api/writing/chapters/${chapterId}`),
    updateChapter: (
      chapterId: string,
      data: { title?: string; content?: string }
    ) =>
      patch<{ success: boolean }>(`/api/writing/chapters/${chapterId}`, data),
    deleteChapter: (chapterId: string) =>
      del<{ success: boolean }>(`/api/writing/chapters/${chapterId}`),

    listNotes: (projectId: string) =>
      get<WritingNoteSummary[]>(`/api/writing/projects/${projectId}/notes`),
    createNote: (
      projectId: string,
      data: { title: string; content?: string; docType?: string }
    ) => post<{ id: string }>(`/api/writing/projects/${projectId}/notes`, data),
    getNote: (noteId: string) =>
      get<WritingNote>(`/api/writing/notes/${noteId}`),
    updateNote: (
      noteId: string,
      data: { title?: string; content?: string; docType?: string }
    ) => patch<{ success: boolean }>(`/api/writing/notes/${noteId}`, data),
    deleteNote: (noteId: string) =>
      del<{ success: boolean }>(`/api/writing/notes/${noteId}`),

    listDiscussions: (projectId: string) =>
      get<Conversation[]>(`/api/writing/projects/${projectId}/conversations`),
    createDiscussion: (projectId: string, data?: { title?: string }) =>
      post<{ id: string }>(
        `/api/writing/projects/${projectId}/conversations`,
        data ?? {}
      ),
    summarizeDiscussion: (discussionId: string) =>
      post<WritingNote>(`/api/writing/conversations/${discussionId}/summarize`),
  },

  curatedTags: {
    list: () => get<CuratedTag[]>('/api/curated-tags'),
    create: (name: string) =>
      post<{ id: string }>('/api/curated-tags', { name }),
    rename: (id: string, name: string) =>
      patch<{ success: boolean }>(`/api/curated-tags/${id}`, { name }),
    delete: (id: string) =>
      del<{ success: boolean }>(`/api/curated-tags/${id}`),
    scanStatus: (id: string) =>
      get<{ total: number; processed: number; done: boolean }>(
        `/api/curated-tags/${id}/scan-status`
      ),
  },

  tasks: {
    list: () => get<DailyTask[]>('/api/tasks'),
    create: (title: string) => post<{ id: string }>('/api/tasks', { title }),
    update: (id: string, title: string) =>
      patch<{ success: boolean }>(`/api/tasks/${id}`, { title }),
    reorder: (order: string[]) =>
      post<{ success: boolean }>('/api/tasks/reorder', { order }),
    remove: (id: string) => del<{ success: boolean }>(`/api/tasks/${id}`),
    complete: (id: string) =>
      post<{ success: boolean }>(`/api/tasks/${id}/complete`),
    uncomplete: (id: string) =>
      del<{ success: boolean }>(`/api/tasks/${id}/complete`),
  },

  todos: {
    list: () => get<TodoItem[]>('/api/tasks/todos'),
    create: (title: string) =>
      post<{ id: string }>('/api/tasks/todos', { title }),
    update: (id: string, data: { title?: string; done?: boolean }) =>
      patch<{ success: boolean }>(`/api/tasks/todos/${id}`, data),
    remove: (id: string) => del<{ success: boolean }>(`/api/tasks/todos/${id}`),
  },

  newspapers: {
    getByDate: (date: string) =>
      get<FrontPage[]>(`/api/newspapers/frontpages/${date}`),
    sync: () => post<SyncResult[]>('/api/newspapers/sync'),
  },
};
