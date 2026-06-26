// Typed API client — replaces tRPC hooks

export interface JournalEntry {
  id: string;
  content: string;
  title: string | null;
  tags: string | null;
  createdAt: string;
  updatedAt: string;
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

export interface Flashcard {
  id: string;
  front: string;
  back: string;
  sourceId: string | null;
  easiness: number;
  interval: number;
  repetitions: number;
  nextReview: string;
  createdAt: string;
}

export interface FlashcardStats {
  total: number;
  due: number;
  mastered: number;
  learning: number;
}

export interface Conversation {
  id: string;
  title: string | null;
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
  aiProvider: string | null;
  aiModel: string | null;
  hasOpenaiKey: boolean;
  hasGoogleKey: boolean;
  ollamaUrl: string | null;
  ollamaModel: string | null;
  networkMode: boolean;
  networkCode: string | null;
  sttPasteKey: string | null;
  sttVoiceKey: string | null;
  sttBackend: string | null;
  ttsBackend: string | null;
  whisperModel: string | null;
}

export interface WhisperModel {
  name: string;
  vramMb: number;
}

export interface OllamaModel {
  name: string;
  vramMb: number;
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

export interface WritingContextDocSummary {
  id: string;
  projectId: string;
  title: string;
  docType: 'character' | 'outline' | 'worldbuilding' | 'note';
  createdAt: string;
  updatedAt: string;
}

export interface WritingContextDoc extends WritingContextDocSummary {
  content: string;
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

async function send<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    credentials: 'include',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
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
const del = <T>(url: string) => send<T>('DELETE', url);

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
    updateAI: (data: Partial<AppSettings & { openaiApiKey?: string; googleApiKey?: string }>) =>
      patch<{ success: boolean }>('/api/settings/ai', data),
    updateShortcuts: (data: { sttPasteKey?: string; sttVoiceKey?: string }) =>
      patch<{ success: boolean }>('/api/settings/ai', data),
    regenerateCode: () => post<{ networkCode: string }>('/api/settings/regenerate-code'),
    ollamaModels: () => get<OllamaModel[]>('/api/settings/ollama-models'),
  },

  journal: {
    list: (params?: { limit?: number; offset?: number }) =>
      get<JournalEntry[]>(`/api/journal?${new URLSearchParams(params as Record<string, string> || {})}`),
    search: (query: string, limit?: number) =>
      get<JournalEntry[]>(`/api/journal/search?query=${encodeURIComponent(query)}&limit=${limit ?? 50}`),
    semanticSearch: (query: string, limit?: number) =>
      get<JournalEntry[]>(`/api/journal/semantic-search?query=${encodeURIComponent(query)}&limit=${limit ?? 5}`),
    get: (id: string) => get<JournalEntry>(`/api/journal/${id}`),
    create: (data: { content: string; title?: string; tags?: string[] }) =>
      post<{ id: string }>('/api/journal', data),
    update: (id: string, data: { content?: string; title?: string; tags?: string[] }) =>
      patch<{ success: boolean }>(`/api/journal/${id}`, data),
    delete: (id: string) => del<{ success: boolean }>(`/api/journal/${id}`),
  },

  calendar: {
    listByRange: (start: string, end: string) =>
      get<CalendarEvent[]>(`/api/calendar?start=${start}&end=${end}`),
    listByDate: (date: string) => get<CalendarEvent[]>(`/api/calendar/date/${date}`),
    listByWeek: (date: string) => get<CalendarEvent[]>(`/api/calendar/week/${date}`),
    get: (id: string) => get<CalendarEvent>(`/api/calendar/${id}`),
    findRelatedJournals: (date: string) =>
      get<JournalEntry[]>(`/api/calendar/related-journals/${date}`),
    create: (data: {
      title: string; date: string; description?: string; time?: string;
      endTime?: string; tags?: string[]; journalId?: string;
    }) => post<{ id: string }>('/api/calendar', data),
    update: (id: string, data: Record<string, unknown>) =>
      patch<{ success: boolean }>(`/api/calendar/${id}`, data),
    delete: (id: string) => del<{ success: boolean }>(`/api/calendar/${id}`),
    linkJournal: (id: string, journalEntryId: string) =>
      post<{ id: string }>(`/api/calendar/${id}/link`, { journalEntryId }),
    unlinkJournal: (id: string, journalEntryId: string) =>
      del<{ success: boolean }>(`/api/calendar/${id}/link/${journalEntryId}`),
  },

  flashcard: {
    list: (params?: { limit?: number; offset?: number }) =>
      get<Flashcard[]>(`/api/flashcards?${new URLSearchParams(params as Record<string, string> || {})}`),
    getDue: () => get<Flashcard[]>('/api/flashcards/due'),
    getStats: () => get<FlashcardStats>('/api/flashcards/stats'),
    get: (id: string) => get<Flashcard>(`/api/flashcards/${id}`),
    getBySource: (sourceId: string) => get<Flashcard[]>(`/api/flashcards/by-source/${sourceId}`),
    create: (data: { front: string; back: string; sourceId?: string }) =>
      post<{ id: string }>('/api/flashcards', data),
    review: (id: string, grade: number) =>
      post<{ nextReview: string; interval: number }>(`/api/flashcards/${id}/review`, { grade }),
    update: (id: string, data: { front?: string; back?: string }) =>
      patch<{ success: boolean }>(`/api/flashcards/${id}`, data),
    delete: (id: string) => del<{ success: boolean }>(`/api/flashcards/${id}`),
    generateFromJournal: (journalId: string) =>
      post<{ count: number; ids: string[] }>('/api/flashcards/generate-from-journal', { journalId }),
    generateForTopic: (topic: string) =>
      post<{ count: number; ids: string[] }>('/api/flashcards/generate-for-topic', { topic }),
  },

  chat: {
    listConversations: () => get<Conversation[]>('/api/chat/conversations'),
    getConversation: (id: string) =>
      get<ConversationWithMessages | null>(`/api/chat/conversations/${id}`),
    createConversation: (data?: { title?: string }) =>
      post<{ id: string }>('/api/chat/conversations', data ?? {}),
    updateTitle: (id: string, title: string) =>
      patch<{ success: boolean }>(`/api/chat/conversations/${id}/title`, { title }),
    deleteConversation: (id: string) =>
      del<{ success: boolean }>(`/api/chat/conversations/${id}`),
    addMessage: (id: string, data: { role: string; content: string; metadata?: string }) =>
      post<{ id: string }>(`/api/chat/conversations/${id}/messages`, data),
    classify: (message: string) =>
      post<{ intent: string; confidence: number; [key: string]: unknown }>('/api/chat/classify', { message }),
    saveJournal: (data: {
      conversationId: string; messageId?: string;
      title: string; content: string; tags: string[];
    }) => post<{ id: string }>('/api/chat/save-journal', data),
    saveCalendar: (data: {
      conversationId: string; messageId?: string;
      title: string; description: string; date: string; time?: string; tags: string[];
    }) => post<{ id: string }>('/api/chat/save-calendar', data),
    ragContext: (message: string, limit?: number) =>
      post<RAGContext>('/api/chat/rag-context', { message, limit: limit ?? 3 }),
  },

  files: {
    list: (path?: string) =>
      get<FileEntry[]>(`/api/files?${path ? `path=${encodeURIComponent(path)}` : ''}`),
    read: (path: string) =>
      get<{ content: string }>(`/api/files/read?path=${encodeURIComponent(path)}`),
    write: (path: string, content: string) =>
      post<{ success: boolean }>('/api/files/write', { path, content }),
    rename: (from: string, to: string) =>
      post<{ success: boolean }>('/api/files/rename', { from, to }),
    delete: (path: string) =>
      del<{ success: boolean }>(`/api/files?path=${encodeURIComponent(path)}`),
  },

  stt: {
    health: () => get<{ stt_backend: string; stt_model: string; stt_ready: boolean; tts_backend: string; tts_ready: boolean }>('/api/stt/health'),
    whisperModels: () => get<WhisperModel[]>('/api/stt/whisper-models'),
    reload: () => post<{ success: boolean }>('/api/stt/reload'),
    listenerState: () => get<{ recording: boolean; transcribing: boolean; mode: string | null }>('/api/stt/listener-state'),
  },

  rag: {
    isConfigured: () => get<boolean>('/api/rag/configured'),
    getStats: () => get<{ totalJournals: number; indexedJournals: number; totalChunks: number; isConfigured: boolean }>('/api/rag/stats'),
    syncJournal: (journalId: string) => post<{ chunks: number }>(`/api/rag/sync/${journalId}`),
    syncAll: () => post<{ synced: number; chunks: number }>('/api/rag/sync-all'),
    search: (query: string, limit?: number) =>
      get<RAGResult[]>(`/api/rag/search?query=${encodeURIComponent(query)}&limit=${limit ?? 5}`),
  },

  writing: {
    listProjects: () => get<WritingProject[]>('/api/writing/projects'),
    createProject: (data: { title: string; description?: string }) =>
      post<{ id: string }>('/api/writing/projects', data),
    getProject: (id: string) => get<WritingProject>(`/api/writing/projects/${id}`),
    updateProject: (id: string, data: { title?: string; description?: string }) =>
      patch<{ success: boolean }>(`/api/writing/projects/${id}`, data),
    deleteProject: (id: string) => del<{ success: boolean }>(`/api/writing/projects/${id}`),

    listChapters: (projectId: string) =>
      get<WritingChapterSummary[]>(`/api/writing/projects/${projectId}/chapters`),
    createChapter: (projectId: string, data: { title: string }) =>
      post<{ id: string }>(`/api/writing/projects/${projectId}/chapters`, data),
    getChapter: (chapterId: string) => get<WritingChapter>(`/api/writing/chapters/${chapterId}`),
    updateChapter: (chapterId: string, data: { title?: string; content?: string }) =>
      patch<{ success: boolean }>(`/api/writing/chapters/${chapterId}`, data),
    deleteChapter: (chapterId: string) => del<{ success: boolean }>(`/api/writing/chapters/${chapterId}`),

    listContextDocs: (projectId: string) =>
      get<WritingContextDocSummary[]>(`/api/writing/projects/${projectId}/context-docs`),
    createContextDoc: (projectId: string, data: { title: string; content: string; docType: string }) =>
      post<{ id: string }>(`/api/writing/projects/${projectId}/context-docs`, data),
    getContextDoc: (docId: string) => get<WritingContextDoc>(`/api/writing/context-docs/${docId}`),
    updateContextDoc: (docId: string, data: { title?: string; content?: string; docType?: string }) =>
      patch<{ success: boolean }>(`/api/writing/context-docs/${docId}`, data),
    deleteContextDoc: (docId: string) => del<{ success: boolean }>(`/api/writing/context-docs/${docId}`),

    listProjectConversations: (projectId: string) =>
      get<Conversation[]>(`/api/writing/projects/${projectId}/conversations`),
    createProjectConversation: (projectId: string, data?: { title?: string }) =>
      post<{ id: string }>(`/api/writing/projects/${projectId}/conversations`, data ?? {}),
  },
};
