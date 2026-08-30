// Typed API client — replaces tRPC hooks

import { reportFetchOutcome } from '../offline/onlineManager';
import {
  recordingFilename,
  uploadFilenameFor,
} from '../lib/journalAttachments';
// The shape is defined next to the geometry that consumes it, so the payload
// and the band math can't drift apart.
import type { SleepDay } from '../lib/sleep';
// Same reasoning: the log-entry shape lives next to the filtering logic that
// consumes it, and the API client just re-exports it.
import type {
  ServerLogEntry,
  ServerLogResponse,
  ServerLogUnit,
} from '../lib/serverLogs';

export type { SleepDay };
export type { ServerLogEntry, ServerLogResponse, ServerLogUnit };

export interface JournalEntry {
  id: string;
  content: string;
  rawContent: string | null;
  title: string | null;
  tags: string | null;
  curatedTags: string[];
  ficRefs?: FicRef[];
  attachments?: JournalAttachment[];
  createdAt: string;
  updatedAt: string;
}

export interface JournalAttachment {
  id: string;
  entryId: string;
  kind: 'audio' | 'video' | 'image';
  /** The user's label — what this recording, video or photo is about. */
  name: string;
  url: string;
  mime: string | null;
  size: number | null;
  position: number;
  /** Transcript for audio, caption for an image. Null until asked for. */
  transcript: string | null;
  transcriptStatus: 'idle' | 'running' | 'done' | 'error';
  transcriptError: string | null;
  /** Non-speech audio content (ambient sound), audio/video only. Null until asked for. */
  description: string | null;
  descriptionStatus: 'idle' | 'running' | 'done' | 'error';
  descriptionError: string | null;
  /** EXIF-derived capture location, images only. Null when the photo carries no GPS EXIF. */
  latitude: number | null;
  longitude: number | null;
  createdAt: string;
}

export interface FicRef {
  ficId: string;
  ficTitle: string;
  chapterId: string | null;
  chapterTitle: string | null;
}

/** One STT backend's output for a voice draft, kept for the dropdown even
 * after promotion — {text} on success, {error} on failure. */
export interface JournalVoiceDraftCandidate {
  backend: string;
  text?: string;
  error?: string;
}

/** A clip recorded via the STT listener's Journal hotkey, before it resolves
 * into an entry. Several local STT backends transcribe it in the background
 * and the LLM reconciles their outputs — see backend/journal/voice_drafts.py.
 * A 'done' draft has already become a normal JournalEntry. */
export interface JournalVoiceDraft {
  id: string;
  url: string;
  mime: string | null;
  size: number | null;
  status: 'processing' | 'done' | 'error';
  error: string | null;
  candidates: JournalVoiceDraftCandidate[];
  entryId: string | null;
  createdAt: string;
  completedAt: string | null;
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
  deepPending?: boolean;
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

export interface FicBookmark {
  id: string;
  ficId: string;
  chapterId: string;
  chapterTitle: string;
  type: 'favorite' | 'continue';
  scrollPosition: number;
  createdAt: string;
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

export interface WatchedScanProgress {
  page: number;
  lastPage: number | null;
  found: number;
  imported: number;
  alreadyInLibrary: number;
  done: boolean;
  error: string | null;
}

export interface SiteCookieInfo {
  domain: string;
  hasCookie: boolean;
  updatedAt: string | null;
  hasUserAgent: boolean;
  watchedScan?: WatchedScanProgress;
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

export interface RecipeMedia {
  id: string;
  kind: 'image' | 'video' | 'audio';
  position: number;
  url: string;
}

export interface Recipe {
  id: string;
  title: string;
  content: string;
  tags: string | null;
  sourceUrl: string | null;
  media: RecipeMedia[];
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
  // Explicitly all-day, as opposed to merely untimed. Arrives from SQLite as
  // 0/1, like every other boolean column in this API.
  allDay: boolean;
  tags: string | null;
  journalId: string | null;
  createdAt: string;
  // Recurrence rule. repeatFreq null = a one-off event.
  repeatFreq: 'daily' | 'weekly' | 'monthly' | 'yearly' | null;
  repeatInterval: number | null;
  repeatByweekday: string | null; // CSV of 0-6, Sunday=0
  repeatUntil: string | null;
  // AI-assigned categories (leisure/work/exercise/family/outside/indoors),
  // JSON array string — parse with parseCategoryTags from lib/calendarCategories.
  // Separate from the free-text `tags` above so a classifier result can never
  // collide with a user-typed pill. classifiedAt null = still pending, for
  // both never-transcribed events and a previously-failed classification.
  categoryTags: string | null;
  classifiedAt: string | null;
  classificationError: string | null;
  // Set on events returned by the range/date/week endpoints, which expand
  // recurring series: `id` stays the series id, `occurrenceDate` identifies
  // this instance. Absent from the single-event GET, which returns the series.
  occurrenceDate?: string;
  isRecurring?: boolean;
  linkedJournals?: JournalEntry[];
}

export interface CalendarRepeat {
  repeatFreq?: 'daily' | 'weekly' | 'monthly' | 'yearly' | null;
  repeatInterval?: number | null;
  repeatByweekday?: number[] | null;
  repeatUntil?: string | null;
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
  /** 1-2 sentence, code-free version of the mistake for text-to-speech.
   *  Only present when the answer was submitted with speech mode on. */
  speechSummary?: string;
}

export interface GradeResult {
  coverage: ClaimCoverage;
  suggestedRating: number;
  normalizedAnswer: string;
}

/** A card answered (or flipped past) but not yet rated — the persisted state
 *  of an in-progress review session. Deleted when the rating is committed. */
export interface LearningAttempt {
  id: string;
  cardId: string;
  mode: 'answered' | 'skipped';
  answer: string | null;
  answerMode: 'typed' | 'voice' | 'self' | null;
  gradeStatus: 'pending' | 'done' | 'error' | 'skipped';
  coverage: ClaimCoverage | null;
  suggestedRating: number | null;
  normalizedAnswer: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ApproveResult {
  status: 'approved' | 'duplicateHint';
  due?: string;
  similar?: { id: string; question: string; answer: string };
  score?: number;
}

export interface DraftCard {
  id: string;
  question: string;
  answer: string;
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
  mode?: ChatMode;
  createdAt: string;
  updatedAt: string;
}

// A photo attached to a chat message. The chat model is text-only, so
// `description` — written by the CPU-only omni model — is how the picture
// actually reaches the conversation. Uploaded before the message exists, hence
// the null `messageId` while it is still staged in the composer.
export interface ChatAttachment {
  id: string;
  conversationId: string;
  messageId: string | null;
  mime: string | null;
  url: string;
  description: string | null;
  descriptionStatus: 'running' | 'done' | 'error' | null;
  descriptionError: string | null;
  /** Where the device was when the photo was attached — the fallback behind the
   * photo's own EXIF GPS, which a pasted image no longer has. */
  latitude: number | null;
  longitude: number | null;
  position: number;
  createdAt: string;
}

export interface Message {
  id: string;
  conversationId: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  metadata: string | null;
  // 'streaming' while a background run is still generating this reply
  // (backend/delegate/runs.py) — absent/undefined on older cached data.
  status?: 'streaming' | 'done' | 'error';
  error?: string | null;
  // What was dictated, before the correction pass and before the user edited it
  // in the composer. Null when the message was typed — same contract as
  // journalEntry.rawContent, and never overwritten.
  rawContent?: string | null;
  attachments?: ChatAttachment[];
  createdAt: string;
  // When generation stopped, for an assistant message. Null on user messages
  // and on rows written before the column existed — the bubble falls back to
  // createdAt, which is what it always showed.
  finishedAt?: string | null;
}

export interface ConversationWithMessages extends Conversation {
  messages: Message[];
}

// A to-do the briefing suggested. It lives in the briefing message's metadata
// until the user accepts it (which creates the real todo, reusing this id) or
// rejects it.
// One item on a *historical* briefing's plan for the day, frozen at whatever
// status it resolved to before this shape was retired (the briefing now
// writes straight into ChatTodoItem/chat_todos, no accept step, no per-item
// status). Lives only in old chat messages' metadata — Journal.tsx renders it
// read-only, since there is no backend left to accept/reject/dismiss it. `list`
// is the old permanent-todos list it would have joined, and `duplicate` is
// legacy: briefings written before linking existed can still carry it, and the
// link fields are absent on those.
export interface ProposedTodo {
  id: string;
  title: string;
  list: TodoList;
  priority: number;
  due: number | null;
  status: 'pending' | 'done' | 'accepted' | 'rejected' | 'duplicate';
  // The existing to-do / daily task this item restated, when there was one.
  linkedType?: 'todo' | 'daily' | null;
  linkedId?: string | null;
  linkedTitle?: string | null;
  resolvedAt?: number | null;
}

// A delegate confirm card — calendar/calorie/food/recipe/flashcards from a
// live chat turn, plus recipe_link from the background homemade-match check
// (backend/food/recipe_match.py). `note` proposals draft immediately with no
// confirm step, so they never get one of these (backend/delegate/runs.py) —
// nor does a to-do, which now writes straight into ChatTodoItem/chat_todos via
// the add_todos tool. Written into the
// assistant message's metadata the moment the run that staged it finishes (or,
// for recipe_link, the moment the background check finds a match), and
// resolved in place by POST /api/chat/proposals/<messageId>/<id> — the only
// place `status` ever changes, so a card survives a reload until it actually is.
export interface DelegateProposalRecord {
  id: string;
  kind:
    'calendar' | 'calorie' | 'food' | 'recipe' | 'recipe_link' | 'flashcards';
  data: Record<string, unknown>;
  status: 'pending' | 'accepted' | 'dismissed';
  resolvedAt?: number;
  // What accepting produced — {id} for calendar/calorie, {count} for
  // flashcards, {id, photos, calorieLogId} for food — so the resolved state
  // renders from metadata alone.
  result?: {
    id?: string;
    count?: number;
    photos?: number;
    calorieLogId?: string;
  };
}

// One conversation per chat day. `'websearch'` is a *historical* value only —
// the tab that created those conversations is gone (searching is now something
// the delegate decides to do mid-chat), but the rows remain and the Journal
// feed still labels them, so this stays in the union as read-only history.
export type ChatMode = 'chat' | 'websearch';

// A past chat day shown in the Journal feed (collapsed, expand to load messages).
export interface DatedConversation {
  id: string;
  title: string | null;
  dayKey: string;
  mode: ChatMode;
  messageCount: number;
  createdAt: string;
  updatedAt: string;
}

// One free-text document of standing facts, read into every chat system prompt.
export interface UserMemory {
  content: string;
  maxChars: number;
}

// A snapshot of the document as it stood *before* one change — copy-on-write,
// the wiki_revisions pattern. This is what makes an unconfirmed write by the
// assistant safe to allow.
export interface MemoryRevision {
  id: string;
  content: string;
  source: 'remember' | 'revise' | 'user' | 'restore';
  note: string | null;
  createdAt: string;
}

export interface AppSettings {
  hasHfToken: boolean;
  llamaUrl: string | null;
  /** A llama-server router alias — a section name in llama/presets.ini, not a
   * file name. */
  llamaModel: string | null;
  /** Separate alias for image captioning: the chat presets skip Gemma 4's vision
   * tower to fit in VRAM, so an empty string means photo captioning is off. */
  llamaVisionModel: string;
  /** Separate alias for non-speech audio description: audio input is an
   * E2B/E4B/12B capability, not the 26B chat model, so this names a
   * different preset entirely. Empty means it's off. */
  llamaAudioModel: string;
  /** Whether the chat model itself is handed photos attached in Chat, rather
   * than being read a description of them. Qwen3.6 is a vision-language model,
   * but `[qwen36]` ships with no projector — so this stays off until an mmproj
   * is configured and the preset has been confirmed to still load. */
  llamaChatVision: boolean;
  /** Gemma 4's thinking channel is on or off; there are no graded levels. */
  llmThinking: boolean;
  llmMaxTokens: number;
  networkMode: boolean;
  networkCode: string | null;
  sttPasteKey: string | null;
  sttVoiceKey: string | null;
  sttJournalKey: string | null;
  sttBackend: string | null;
  ttsBackend: string | null;
  whisperModel: string | null;
  sttDevice: string | null;
  voicePipelineEnabled: boolean;
  /** Whether every dictation surface transcribes with two STT models and has
   * the LLM reconcile them, instead of taking the single configured backend.
   * Off is faster; on is markedly more accurate on mishearings. Despite the
   * name it now switches the whole strategy, not just a cleanup pass. */
  transcribePolishEnabled: boolean;
  preventSleep: boolean;
  meetingEchoCancel: boolean;
  nudgeEnabled: boolean;
  nudgeIntervalMinutes: number;
  briefingEnabled: boolean;
  briefingHour: number;
  briefingModel: string | null;
  briefingGoals: string;
  briefingThinking: boolean;
  briefingMaxTokens: number;
  /** '' | 'brave' | 'searxng' — empty means the web-search chat tab
   * degrades to an explanatory failure instead of searching. */
  websearchSearchProvider: string;
  hasWebsearchSearchKey: boolean;
  websearchSearxngUrl: string;
  repoContextEnabled: boolean;
  repoContextHour: number;
  /** Module notes the nightly pass writes per repo. 0 turns it off. */
  codeWikiArticles: number;
  researchEnabled: boolean;
  researchSearchProvider: string;
  hasResearchSearchKey: boolean;
  researchSearxngUrl: string;
  // Wall-clock budgets, in seconds. The chat one bounds a whole reply; the
  // two research ones bound gathering, after which the run writes up what it
  // already has rather than failing (backend/delegate/limits.py).
  chatTimeoutEnabled: boolean;
  chatTimeoutSeconds: number;
  researchTimeoutEnabled: boolean;
  researchSearchTimeoutSeconds: number;
  researchDeepTimeoutSeconds: number;
  hasGoogleOauthClient: boolean;
  hasMicrosoftOauthClient: boolean;
  /** Fallback location for the weather card when no geolocation fix has ever
   * been logged. Null lat/lon means it's unset. */
  weatherDefaultLat: number | null;
  weatherDefaultLon: number | null;
  weatherDefaultLabel: string;
  emailSyncEnabled: boolean;
  emailSyncIntervalMinutes: number;
  // Tailored-resume retention: whichever of the two clocks runs out first.
  jobTriageEnabled: boolean;
  jobRetentionDays: number;
  jobPurgeOnRejection: boolean;
  jobRejectionGraceDays: number;
  /** The key itself never leaves the server, so only its presence is exposed. */
  hasAdzunaCredentials: boolean;
}

export interface WhisperModel {
  name: string;
  vramMb: number;
}

export interface LlamaModel {
  name: string;
  /** Router lifecycle: unloaded | loading | loaded | downloading | sleeping. */
  status: string;
  inputModalities: string[];
  /** Only known once the model is loaded. */
  contextLength: number | null;
}

export interface GpuVram {
  available: boolean;
  /** Non-LLM usage, measured once at backend startup. */
  baseMb?: number;
  totalMb?: number;
  /** Live total, and the share held by llama-server. */
  usedMb?: number | null;
  llmMb?: number;
}

/** Health of the nightly backup job — see backend/routes/backup.py. */
export type BackupHealth =
  | 'ok'
  | 'stale'
  | 'unreachable'
  | 'readonly'
  | 'permissions'
  | 'empty'
  | 'unconfigured';

export interface BackupConfig {
  path: string;
  retentionDays: number;
  /** Which source supplied `path`: the settings table, the legacy
   *  ops/backup.env fallback, or nothing at all. */
  source: 'settings' | 'backup.env' | 'unset';
}

export interface BackupBrowseEntry {
  name: string;
  path: string;
  writable: boolean;
}

export interface BackupBrowse {
  path: string;
  parent: string | null;
  entries: BackupBrowseEntry[];
  truncated: boolean;
  writable: boolean;
  isMount: boolean;
  suggestions: { name: string; path: string }[];
}

export interface BackupRun {
  running: boolean;
  startedAt: string | null;
  finishedAt: string | null;
  ok: boolean | null;
  output: string | null;
}

export interface BackupStatus {
  configured: boolean;
  destination: string | null;
  /** The directory tested to decide the drive is present — the parent of
   *  `destination`, matching what ops/backup.sh checks. */
  mountPoint: string | null;
  configSource: BackupConfig['source'];
  retentionDays: number;
  health: BackupHealth;
  /** Human-readable explanations for a non-ok health; empty when ok. */
  problems: string[];
  mount: {
    present: boolean;
    writable: boolean;
    /** True only for a genuinely read-only *mount* (ST_RDONLY) — distinct from
     *  a read-write mount this user merely lacks permission to write to. */
    readonly: boolean;
    freeBytes: number | null;
    totalBytes: number | null;
  };
  snapshots: {
    latest: string | null;
    ageDays: number | null;
    count: number;
    /** What `count` should be given how long this drive has been backing up —
     *  a three-day-old backup holding 3 snapshots is complete, not broken. */
    expectedCount: number;
    latestSizeBytes: number | null;
    dates: string[];
  };
  media: { lastModified: string | null };
  timer: {
    available: boolean;
    enabled: boolean | null;
    lastRun: string | null;
    lastResult: string | null;
    nextRun: string | null;
  };
  run: BackupRun;
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

export interface FilesConfig {
  path: string;
  /** Whether `path` came from Settings → Files, or nothing has been chosen. */
  source: 'settings' | 'unset';
}

export interface FileUploadResult {
  uploaded: { name: string; path: string; size: number }[];
  errors: { name: string; error: string }[];
}

export interface NotebookReviewState {
  path: string;
  enabled: boolean;
  fsrsState: string | null;
  due: string | null;
}

export interface NoteToSelf {
  id: string;
  content: string;
  intervalDays: number;
  due: string;
  createdAt: string;
  updatedAt: string;
}

export interface NoteToSelfRevision {
  id: string;
  noteId: string;
  content: string;
  createdAt: string;
}

export type IdeaStatus =
  | 'new'
  | 'researching'
  | 'ready'
  | 'planned'
  | 'building'
  | 'shipped'
  | 'parked';

export type IdeaVerdict = 'no' | 'partial' | 'yes';

/** List row: the two body columns are omitted server-side. */
export interface IdeaSummary {
  id: string;
  title: string;
  status: IdeaStatus;
  tags: string | null;
  sketchCount: number;
  openQuestionCount: number;
  articleCount: number;
  hasPlan: boolean;
  /** The agent's call. `userVerdict` overrides it wherever both exist. */
  verdict: IdeaVerdict | null;
  confidence: number | null;
  effort: 's' | 'm' | 'l' | null;
  onRoadmap: boolean;
  /** The repo moved since the verdict was formed. */
  assessmentStale: boolean;
  userVerdict: IdeaVerdict | null;
  researchState: string | null;
  /** Which registered repository this idea is about; null for a plain product idea. */
  repoId: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface Idea extends Omit<
  IdeaSummary,
  | 'sketchCount'
  | 'openQuestionCount'
  | 'articleCount'
  | 'hasPlan'
  | 'verdict'
  | 'confidence'
  | 'effort'
  | 'onRoadmap'
  | 'assessmentStale'
> {
  /** As spoken or typed. Never overwritten — only `content` is AI-owned. */
  rawContent: string;
  content: string;
  userVerdictNote: string | null;
}

/** Evidence the agent cited — chosen by index from a list the server built,
 *  so every entry points at a file that actually exists. */
export interface IdeaEvidence {
  kind: string;
  ref: string;
  file: string | null;
  line: number | null;
  detail: string | null;
}

export interface IdeaAssessment {
  id: string;
  ideaId: string;
  snapshotId: string | null;
  verdict: IdeaVerdict;
  confidence: number;
  rationale: string;
  evidence: IdeaEvidence[];
  onRoadmap: string[];
  effort: 's' | 'm' | 'l' | null;
  stale: boolean;
  assessedAt: string;
}

export interface IdeaQuestion {
  id: string;
  ideaId: string;
  question: string;
  why: string | null;
  options: string[];
  answer: string | null;
  status: 'open' | 'answered' | 'dismissed';
  answeredAt: string | null;
  createdAt: string;
}

export interface IdeaPlanSummary {
  id: string;
  ideaId: string;
  version: number;
  createdAt: string;
  updatedAt: string;
}

export interface IdeaPlan extends IdeaPlanSummary {
  /** Rendered markdown — the thing you hand to a coding agent. */
  content: string;
  spec: string;
}

export interface IdeaSketch {
  id: string;
  ideaId: string;
  pageId: string;
  paperId: string;
  /** What the sketch shows. The agent reads this, not the drawing. */
  caption: string;
  position: number;
  imageUrl: string | null;
  createdAt: string;
}

/** A Paper page offered in the sketch picker. */
export interface IdeaPaperPage {
  pageId: string;
  paperId: string;
  paperTitle: string;
  position: number;
  imageUrl: string | null;
}

/**
 * A repository the Ideas agent can read. Registered by git URL and cloned into
 * ./data/repos/<slug>/ — Luna owns every checkout, so nothing here points at a
 * working tree the user is editing.
 */
export interface Repo {
  id: string;
  slug: string;
  name: string;
  remoteUrl: string;
  /** '' means the remote's default branch, resolved at clone time. */
  branch: string;
  cloneState: 'pending' | 'cloning' | 'ready' | 'error';
  cloneError: string | null;
  headSha: string | null;
  lastPulledAt: string | null;
  /** Null until a graphify graph has been built inside the clone. */
  graphBuiltAt: string | null;
  graphNodeCount: number | null;
  isDefault: boolean;
  /** Filesystem truth, not a column: the clone and the graph really being there. */
  hasCheckout: boolean;
  hasGraph: boolean;
}

/**
 * A nightly, machine-generated picture of what the app currently is. `digest`
 * is deterministic extraction; only `changeSummary` comes from the model, and
 * it is null when the model was unavailable.
 */
export interface RepoSnapshot {
  id: string;
  gitSha: string | null;
  gitBranch: string | null;
  digest: string;
  changeSummary: string | null;
  routeCount: number;
  tableCount: number;
  componentCount: number;
  /** Drift between the frontend's three hand-synced view lists. */
  warnings: string[];
  generatedAt: string;
}

/** What the background research worker is doing right now. */
export interface ResearchStatus {
  running: boolean;
  current: { kind: string; target: string | null; startedAt: number } | null;
  last: {
    kind: string;
    target: string | null;
    error: string | null;
    cancelled: boolean;
    seconds: number;
    finishedAt: number;
  } | null;
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

export type TaskEventKind =
  'todo_completed' | 'daily_completed' | 'task_deleted' | 'chat_todo_completed';

// Which list the task came from: a todo list, 'daily' for a daily task, or
// 'chat' for an item completed from the Chat tab's to-do bar. 'chores' is
// history — the list is retired, but events logged while it existed still
// name it and the Journal feed still labels them.
export type TaskListSource = 'todo' | 'chores' | 'archive' | 'daily' | 'chat';

export interface TaskEvent {
  id: string;
  kind: TaskEventKind;
  title: string;
  refId: string | null;
  taskList: TaskListSource | null;
  detail: string | null;
  createdAt: string;
}

export type TodoList = 'todo' | 'archive';
export type RepeatUnit = 'day' | 'week' | 'month';

export interface TodoItem {
  id: string;
  title: string;
  done: boolean;
  completedAt: string | null;
  list: TodoList;
  notes: string | null;
  due: string | null;
  repeatInterval: number | null;
  repeatUnit: RepeatUnit | null;
  priority: number;
  createdAt: string;
  updatedAt: string;
}

export interface TodoPayload {
  title?: string;
  done?: boolean;
  list?: TodoList;
  notes?: string | null;
  due?: number | null;
  repeatInterval?: number | null;
  repeatUnit?: RepeatUnit | null;
  priority?: number;
}

// A day-scoped, ephemeral to-do shown in the Chat tab's bar above the input
// box. Written instantly by the chat delegate's add_todos tool or the morning
// briefing — there is no accept step. Resets at the next day boundary: the
// list endpoint only ever returns today's rows, so an unfinished item from a
// prior day just stops showing rather than needing to be purged.
export interface ChatTodoItem {
  id: string;
  dayKey: string;
  title: string;
  notes: string | null;
  due: string | null;
  priority: number;
  done: boolean;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ChatTodoPayload {
  title?: string;
  notes?: string | null;
  due?: number | null;
  priority?: number;
  done?: boolean;
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

export type EmailProvider = 'gmail' | 'outlook' | 'imap';

export interface EmailOauthStatus {
  connected: boolean;
  emailAddress?: string | null;
  lastSyncedAt?: string | null;
  lastSyncError?: string | null;
  syncEnabled?: boolean;
}

export interface EmailAccountStatus {
  provider: EmailProvider;
  emailAddress: string;
  connected: boolean;
  lastSyncedAt: string | null;
  lastSyncError: string | null;
  syncEnabled: boolean;
}

export type EmailCategory =
  'job_application' | 'newsletter' | 'notification' | 'personal' | 'other';

export type JobApplicationStatus =
  'sent' | 'rejection' | 'interview_next_step' | 'other_update';

export interface EmailMessage {
  id: string;
  accountId: string;
  providerMessageId: string;
  threadId: string | null;
  subject: string | null;
  sender: string | null;
  senderEmail: string | null;
  snippet: string | null;
  bodyText: string;
  /** Sanitized at import (backend/email/sanitize.py). Empty for plain-text
   *  mail and for anything synced before HTML was stored, which is why
   *  EmailDetail falls back to bodyText. */
  bodyHtml: string;
  receivedAt: string;
  category: EmailCategory | null;
  jobStatus: JobApplicationStatus | null;
  classifiedAt: string | null;
  classificationError: string | null;
}

// --- Job applications ------------------------------------------------------
// `JobApplicationStatus` above is the *email* sub-status set by the classifier
// (backend/ai/email.py). `ApplicationStatus` below is where the application
// itself has got to. They are deliberately different vocabularies —
// backend/jobs/linkage.py's EMAIL_STATUS_MAP is the one place they meet.

export type ApplicationStatus =
  | 'draft'
  | 'ready'
  | 'submitted'
  | 'acknowledged'
  | 'interview'
  | 'offer'
  | 'rejected'
  | 'withdrawn'
  | 'ghosted';

export type ProfileSection =
  'roles' | 'bullets' | 'skills' | 'education' | 'answers';

export interface ProfileLink {
  label: string;
  url: string;
}

export interface JobProfileContact {
  id?: number;
  fullName: string;
  email: string;
  phone: string;
  location: string;
  links: ProfileLink[];
  headline: string;
  summary: string;
  workAuthorization: string;
  salaryExpectation: string;
  noticePeriod: string;
  availabilityDate: string;
  relocationWillingness: string;
  securityClearance: string;
  eeoAnswers: string;
  allowedLocations: string;
  remoteOnly: boolean;
  avoidClearanceRoles: boolean;
  softSalaryFloor: number | null;
  softPreferences: string;
  companyBlacklist: string[];
}

export interface ProfileBullet {
  id: string;
  roleId: string;
  text: string;
  ord: number;
  tags: string[];
}

export interface ProfileRole {
  id: string;
  company: string;
  title: string;
  location: string;
  startLabel: string;
  endLabel: string;
  ord: number;
  bullets: ProfileBullet[];
}

export interface ProfileSkill {
  id: string;
  name: string;
  category: string;
  years: number | null;
  ord: number;
}

export interface ProfileEducation {
  id: string;
  institution: string;
  credential: string;
  field: string;
  startLabel: string;
  endLabel: string;
  notes: string;
  ord: number;
}

export interface ProfileAnswer {
  id: string;
  slug: string | null;
  question: string;
  answer: string;
  ord: number;
  tags: string[];
}

export interface JobProfileBundle {
  profile: JobProfileContact;
  roles: ProfileRole[];
  skills: ProfileSkill[];
  education: ProfileEducation[];
  answers: ProfileAnswer[];
}

export interface JobPosting {
  id: string;
  source: 'manual' | 'adzuna' | 'greenhouse' | 'lever' | 'ashby';
  url: string;
  company: string;
  title: string;
  location: string;
  remote: boolean;
  salaryMin: number | null;
  salaryMax: number | null;
  salaryCurrency: string;
  description: string;
  /** Null means "not scored yet", which is not a score of zero. */
  matchScore: number | null;
  dismissed: boolean;
  postedAt: string | null;
  createdAt: string;
  applicationId?: string | null;
  applicationStatus?: ApplicationStatus | null;
}

/** What `keywords.py` worked out, plus the model's optional paragraph.
 *
 * `matched`/`missing`/`coverage` are computed deterministically at sync and
 * are what the feed sorts on. `assessment` is advisory — generated on demand
 * when a posting is opened, and it never moves the sort order. */
export interface MatchReasons {
  matched: string[];
  missing: string[];
  coverage: number;
  /** Adzuna scores against a truncated snippet rather than the posting body,
   * so its coverage number is provisional. */
  partial?: boolean;
  assessment?: JobAssessment;
  assessedAt?: number;
}

export interface JobAssessment {
  verdict: 'strong' | 'possible' | 'weak';
  rationale: string;
  angle: string;
}

/** What triage decided about a posting.
 *
 * 'pending' is not a failure state — it is what every row shows before the
 * model has reached it, and pending rows stay in the feed so it behaves
 * normally when the model is off. */
export type TriageState = 'pending' | 'kept' | 'rejected' | 'error';

/** How close the posting is, as a coarse bucket rather than a score.
 *
 * Deliberately coarse: it groups the feed, but ordering *within* a group is
 * still the deterministic keyword coverage, so the sort does not reshuffle
 * between refreshes. */
export type TriageFit = 'strong' | 'possible' | 'stretch' | '';

/** Something a careful reader would resent finding out after applying. */
export interface TriageFlag {
  kind:
    | 'seniority_mismatch'
    | 'unpaid'
    | 'commission_only'
    | 'unclear_role'
    | 'contract_only'
    | 'onsite_required'
    | 'security_clearance'
    | 'heavy_travel'
    | 'stack_mismatch';
  detail: string;
}

/** A posting on the triage feed: undismissed, and with no application yet. */
export interface FeedJob extends JobPosting {
  matchReasons: MatchReasons | null;
  triageState: TriageState;
  /** Why it was rejected. Always set when the state is 'rejected', so the
   * filtered list can always explain itself. */
  triageReason: string;
  triageFit: TriageFit;
  /** The condensed posting — two sentences meant to be decided from without
   * opening the original. Empty until the model has read it. */
  triageSummary: string;
  triageFlags: TriageFlag[];
  /** When the verdict was reached. Null until it has been. */
  triageAt: string | null;
  triageError: string | null;
}

export interface TriageStatus {
  enabled: boolean;
  pending: number;
  rejected: number;
  failed: number;
  running: boolean;
  current: { jobId: string; startedAt: number } | null;
  last: { jobId: string; error: string | null; seconds: number } | null;
}

/** A resume read into profile shape, before anything is written.
 *
 * `bullets[].text` is reconstructed verbatim from the source document — the
 * model returns line numbers and has no field in which to return prose. */
export interface ResumeImportPreview {
  contact: {
    fullName: string;
    email: string;
    phone: string;
    location: string;
    headline: string;
  };
  roles: {
    company: string;
    title: string;
    location: string;
    startLabel: string;
    endLabel: string;
    bullets: { index: number; text: string }[];
  }[];
  skills: string[];
  education: {
    institution: string;
    credential: string;
    field: string;
    startLabel: string;
    endLabel: string;
  }[];
  lineCount: number;
  /** Lines the parser did not place anywhere, so a dropped accomplishment is
   * visible rather than just missing. */
  unusedLines: { index: number; text: string }[];
}

export type JobSourceKind = 'adzuna' | 'greenhouse' | 'lever' | 'ashby';

/** What a company's careers page turned out to be.
 *
 * `kind` set means it is syncable and `jobCount` came back from the live
 * board. `detected` without `kind` is a recognised ATS this app cannot read —
 * an answer, not a failure. */
export interface CompanyResolution {
  url: string;
  kind: JobSourceKind | null;
  slug: string;
  company: string;
  jobCount: number;
  /** Name of a recognised but unsyncable ATS, e.g. "Workday". */
  detected: string;
  error: string;
  candidates: { kind: JobSourceKind; slug: string }[];
}

export interface JobSearch {
  id: string;
  kind: JobSourceKind;
  label: string;
  /** Adzuna takes {what, where, distanceKm, maxDaysOld, remoteOnly}; the
   * company boards take {slug}. */
  params: Record<string, unknown>;
  enabled: boolean;
  intervalHours: number;
  lastRunAt: string | null;
  lastCount: number | null;
  lastError: string | null;
}
export interface CareerPageWatch {
  id: string;
  url: string;
  label: string;
  enabled: boolean;
  intervalHours: number;
  lastRunAt: string | null;
  lastCount: number | null;
  lastError: string | null;
}
export interface WorkdayBoard extends CareerPageWatch {
  params: string;
}

/** Named for the module because `SyncResult` is already the newspapers one. */
export interface JobSyncResult {
  searchId: string;
  kind?: JobSourceKind;
  added: number;
  updated: number;
  error: string | null;
  message: string;
}

export interface QueueStatus {
  running: boolean;
  current: { applicationId: string; startedAt: number } | null;
  last: {
    applicationId: string;
    error: string | null;
    seconds: number;
    finishedAt: number;
  } | null;
  /** Waiting for a resume. Excludes failures — those need a re-queue. */
  pending: number;
  failed: number;
}

export interface JobApplication {
  id: string;
  jobId: string;
  status: ApplicationStatus;
  steer: string;
  coverLetter: string;
  coverLetterRequired: boolean;
  notes: string;
  appliedEmail: string;
  appliedAt: string | null;
  closedAt: string | null;
  purgeAfter: string | null;
  purgedAt: string | null;
  /** Set when queued from the feed. A queued application stays 'draft' until
   * the worker has actually produced a resume, so this is not the status. */
  queuedAt: string | null;
  /** Why the queue worker gave up. Non-null means it will not be retried
   * without an explicit re-queue. */
  queueError: string | null;
  company: string;
  title: string;
  jobUrl: string;
  location: string;
}

export interface KeywordReport {
  matched: string[];
  /** Ordered by how often the posting mentions the term. */
  missing: string[];
  coverage: number;
}

export interface TailoredBullet {
  bulletId: string;
  roleId: string;
  index: number;
  company: string;
  roleTitle: string;
  /** Kept alongside `text` so the UI can show what the model changed — the
   *  index bound stops invented experience, not an inflated rewrite. */
  original: string;
  text: string;
  rewritten: boolean;
}

export interface TailoredContent {
  summary: string;
  selectedBullets: TailoredBullet[];
  emphasis: string[];
  keywords: KeywordReport;
  /** Issues found by the single bounded reviewer pass before this revision. */
  draftReview?: string[];
}

export interface ResumeVersion {
  id: string;
  applicationId: string;
  label: string;
  content: TailoredContent;
  html: string;
  pdfPath: string | null;
  docxPath: string | null;
  purgedAt: string | null;
  createdAt: string;
  review: ResumeReview;
}

export interface ResumeReview {
  pdfChecked: boolean;
  parseable: boolean | null;
  contactChecks: Record<string, boolean>;
  readingOrder: boolean | null;
  keywordChecks: { expected: string[]; extracted: string[]; coverage: number };
  metrics: {
    bulletCount: number;
    actionVerbDensity: number;
    quantifiedImpactDensity: number;
    sectionSanity: boolean | null;
  };
  issues: string[];
}

export interface TailorResult {
  id: string;
  content: TailoredContent;
  html: string;
  pdfAvailable: boolean;
  docxAvailable: boolean;
  renderers: { pdf: boolean; docx: boolean };
  review: ResumeReview;
}

export interface LinkedEmail {
  id: string;
  subject: string | null;
  sender: string | null;
  senderEmail: string | null;
  receivedAt: string;
  jobStatus: JobApplicationStatus | null;
  linkKind: 'auto' | 'manual';
  confidence: number;
}

export interface JobApplicationDetail extends JobApplication {
  description: string;
  resumes: ResumeVersion[];
  emails: LinkedEmail[];
  recordedAnswers: RecordedAnswer[];
  statusEvents: {
    status: ApplicationStatus;
    source: string;
    sourceId: string | null;
    occurredAt: string;
  }[];
  fillRuns: {
    id: string;
    pageUrl: string;
    pageTitle: string;
    fields: { label: string; answer: string; source: string }[];
    screenshotUrl: string | null;
    createdAt: string;
  }[];
}

export interface ApplicationNoteDraft {
  kind: 'follow_up' | 'thank_you';
  subject: string;
  body: string;
}

export interface InterviewPrepPack {
  id: string;
  roleSummary: string;
  openingPitch: string;
  notes: string;
  questions: {
    question: string;
    kind: 'behavioral' | 'technical' | 'role';
    whyAsked: string;
    storyBulletIds: string[];
    stories: { id: string; company: string; title: string; text: string }[];
    gap: string;
    bridge: string;
  }[];
  questionsForThem: string[];
  watchouts: string[];
  createdAt: string;
}
export interface ApplicationResearch {
  id: string;
  interviewer: string;
  facts: {
    claim: string;
    sourceIndexes: number[];
    sources: { title: string; url: string }[];
  }[];
  interviewAngles: string[];
  sources: { title: string; url: string }[];
  createdAt: string;
}

export interface StaleApplication {
  id: string;
  status: ApplicationStatus;
  appliedAt: string;
  company: string;
  title: string;
  jobUrl: string;
  daysWaiting: number;
}
export interface JobStatusProposal {
  applicationId: string;
  emailId: string;
  currentStatus: ApplicationStatus;
  proposedStatus: ApplicationStatus;
  company: string;
  title: string;
  source: {
    subject: string | null;
    senderEmail: string | null;
    receivedAt: string;
    jobStatus: JobApplicationStatus;
  };
}

/**
 * What was actually typed into one employer's form.
 *
 * Distinct from `ProfileAnswer`, which is the reusable bank: this is the
 * record of what was said, and it outlives the rendered resume on purpose.
 */
export interface RecordedAnswer {
  id: string;
  applicationId: string;
  question: string;
  answer: string;
  source: FilledAnswer['source'] | 'edited';
  /** Which page of a multi-step portal asked it. */
  pageUrl: string;
  ord: number;
  createdAt: string;
  updatedAt: string;
}

/** One bullet's new wording. Structure (company, role, the original text) is
 * not editable — the server takes those from the stored version. */
export interface ResumeBulletEdit {
  bulletId: string;
  text: string;
}

export interface LinkSuggestion {
  applicationId: string;
  company: string;
  title: string;
  score: number;
  reasons: string[];
}

export interface UnlinkedJobEmail extends EmailMessage {
  suggestions: LinkSuggestion[];
}

export type QuestionType =
  'text' | 'textarea' | 'select' | 'boolean' | 'number';

export interface FormQuestion {
  label: string;
  type?: QuestionType;
  options?: string[];
}

export interface FilledAnswer {
  label: string;
  type: QuestionType;
  options: string[];
  answer: string;
  /** Where it came from: 'profile' and 'bank' cost no model call at all. */
  source: 'profile' | 'bank' | 'generated' | 'unanswered';
}

export interface JobStats {
  counts: Record<ApplicationStatus, number>;
  total: number;
  active: {
    id: string;
    status: ApplicationStatus;
    appliedAt: string | null;
    company: string;
    title: string;
  }[];
  unlinkedEmails: number;
  purgingSoon: number;
  funnel: {
    sent: number;
    responded: number;
    responseRate: number;
    averageResponseDays: number | null;
  };
  weekly: { triaged: number; queued: number; sent: number; replies: number };
  sources: {
    source: string;
    applications: number;
    sent: number;
    responded: number;
    responseRate: number;
  }[];
  skills: { term: string; postings: number; ofPostings: number }[];
}
export interface UpskillPlan {
  postings: number;
  generatedAt: string;
  resourcesAvailable: boolean;
  resourceError?: string;
  skills: {
    term: string;
    postings: number;
    ofPostings: number;
    mentions: number;
    centrality: number;
    estimatedHours: number;
    examples: { id: string; title: string; company: string }[];
    resources: {
      title: string;
      url: string;
      snippet: string;
      verifiedBy: string;
    }[];
  }[];
}

export interface EmailStats {
  sentCount: number;
  rejectionCount: number;
  interviewNextStepCount: number;
  otherUpdateCount: number;
  nextSteps: EmailMessage[];
}

export interface EmailImageStatus {
  pending: number;
  stored: number;
  failed: number;
  skipped: number;
  /** False when the external media drive isn't mounted — fetching pauses
   *  rather than failing, so pending work simply waits. */
  storeAvailable: boolean;
  storeRoot: string;
}

export interface EmailSyncResult {
  status: 'ok' | 'error';
  newCount?: number;
  error?: string;
}

export interface PaperDoc {
  id: string;
  title: string;
  pageCount: number;
  firstPageImageUrl: string | null;
  pendingArchive: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface PaperPageMeta {
  id: string;
  position: number;
  imageUrl: string | null;
}

export interface PaperDetail {
  id: string;
  title: string;
  archiveRequested: boolean;
  createdAt: string;
  updatedAt: string;
  pages: PaperPageMeta[];
}

export interface JournalPaper {
  id: string;
  title: string;
  journalDate: string;
  archivedAt: string;
  pages: { id: string; imageUrl: string | null }[];
}

export interface PaperPageImage {
  id: string;
  pageId: string;
  url: string;
  x: number;
  y: number;
  width: number;
  height: number;
  /** Degrees clockwise. */
  rotation: number;
  /** SQLite booleans, 0/1, like every other flag in this API. */
  flipped: number;
  locked: number;
  position: number;
}

export interface PaperPageContent {
  strokes: string;
  width: number | null;
  height: number | null;
  images: PaperPageImage[];
}

export interface FoodMedia {
  id: string;
  kind: 'image' | 'video';
  position: number;
  url: string;
}

export interface FoodEntry {
  id: string;
  rawContent: string | null;
  dish: string | null;
  place: string | null;
  notes: string | null;
  rating: number | null;
  tags: string | null;
  recipeId: string | null;
  recipe: { id: string; title: string } | null;
  media: FoodMedia[];
  latitude: number | null;
  longitude: number | null;
  createdAt: string;
  updatedAt: string;
}

// A food entry shaped for the Journal feed.
export interface FoodJournalItem {
  id: string;
  dish: string | null;
  place: string | null;
  rating: number | null;
  notes: string | null;
  latitude: number | null;
  longitude: number | null;
  createdAt: string;
  recipe: { id: string; title: string } | null;
  media: FoodMedia[];
}

// --- Lifestyle (workouts, heatmap, trends, body weight, selfies, calories) ---
// Tasks are deliberately absent: the Lifestyle tab renders daily tasks and
// to-dos through api.tasks/api.todos rather than a parallel list of its own.

// The activity types, in the priority order the heatmap resolves ties by.
// Kept structurally identical to ACTIVITY_TYPES in src/lib/lifestyle.ts and
// backend/lifestyle/activity.py.
export type ActivityTypeId =
  | 'goodlife_brother'
  | 'goodlife_alone'
  | 'building'
  | 'lifting_home'
  | 'outside';

export interface WorkoutSet {
  id: string;
  /** null = bodyweight ("squats 10 10 10 10"). Never 0 — see formatSets. */
  weight: number | null;
  reps: number | null;
  setOrder: number;
}

export interface WorkoutExercise {
  id: string;
  /** As the user wrote it ("curls"). */
  nameRaw: string;
  /** What it was folded onto, and what the progression chart groups by. */
  nameCanonical: string;
  displayName: string;
  position: number;
  sets: WorkoutSet[];
}

export interface WorkoutSession {
  id: string;
  /** Local 'YYYY-MM-DD', not a timestamp. */
  date: string;
  locationType: ActivityTypeId;
  durationMinutes: number | null;
  /** 1-5 stars, each with a written meaning (src/lib/lifestyle.ts
   *  INTENSITY_LABELS). Was a 1-10 RPE; stored rows were folded once by
   *  _migrate_workout_intensity_to_stars. */
  intensityRating: number | null;
  rawText: string | null;
  notes: string | null;
  /** 'skipped' when there was no text to parse; 'error' means retry reparse. */
  parseStatus: 'pending' | 'done' | 'error' | 'skipped';
  exercises: WorkoutExercise[];
  createdAt: string;
  updatedAt: string;
}

export interface HeatmapDayResponse {
  date: string;
  activityType: ActivityTypeId;
  secondary: boolean;
  durationMinutes: number | null;
  /** The day's hardest session, 1-5 stars. */
  intensityRating: number | null;
  sessions: WorkoutSession[];
}

/** One Monday-start week of the momentum chart. Every week in the window is
 *  sent, zeros included — a skipped quiet week would draw as a flat trend. */
export interface TrendWeek {
  weekStart: string;
  /** Job-application emails classified as sent that week. */
  applications: number;
  journalEntries: number;
}

export interface ExerciseSummary {
  name: string;
  displayName: string;
  sessionCount: number;
  lastDate: string | null;
}

export interface ProgressionPoint {
  date: string;
  /** null for a bodyweight day — nothing was loaded, so there is no top set.
   *  `exerciseSeries` falls back to totalReps rather than plotting a zero. */
  maxWeight: number | null;
  totalVolume: number | null;
  totalReps: number | null;
  setCount: number;
}

export interface ExerciseProgression {
  name: string;
  displayName: string;
  points: ProgressionPoint[];
}

export interface BodyWeightLog {
  id: string;
  date: string;
  weight: number;
  createdAt: string;
  updatedAt: string;
}

export interface Selfie {
  id: string;
  date: string;
  mime: string | null;
  url: string;
  createdAt: string;
}

export interface CalorieLog {
  id: string;
  date: string;
  description: string;
  calories: number;
  createdAt: string;
}

export interface CalorieDay {
  date: string;
  entries: CalorieLog[];
  total: number;
}

export interface WeatherHour {
  id: string;
  dayKey: string;
  hourTs: string;
  weatherCode: number;
  temperatureC: number;
  wetBulbC: number | null;
  humidityPct: number | null;
  /** Whether this hour has already passed — its values are Open-Meteo's
   * observed conditions rather than a forecast. */
  isActual: boolean;
  latitude: number;
  longitude: number;
  locationSource: 'geolocation' | 'default';
}

export interface WeatherLocation {
  latitude: number;
  longitude: number;
  source: 'geolocation' | 'default';
}

export interface WeatherToday {
  hours: WeatherHour[];
  /** null when no geolocation fix has ever been logged and no default
   * location is configured in Settings. */
  location: WeatherLocation | null;
  /** ISO strings, null until a location is known and the day's sun times
   * have synced. */
  sunriseTs: string | null;
  sunsetTs: string | null;
}

export interface PracticeSnippetProgress {
  snippetId: string;
  attemptsCount: number;
  lastWpm: number | null;
  lastAccuracy: number | null;
  bestWpm: number | null;
  bestAccuracy: number | null;
  lastPracticedAt: string | null;
  recallAttemptsCount: number;
  recallPasses: number;
  lastRecallPassed: number | null;
  lastRecallAt: string | null;
  updatedAt: string;
}

// What a snippet is, rather than what it looks like: a sentence, then a line per
// option/field/parameter it uses, then what turns up alongside it. `parts` is a
// list rather than prose so it can be read against the code line by line.
// Nullable because it is keyed by snippet id server-side and a snippet could in
// principle arrive unexplained.
export interface PracticeExplanationPart {
  name: string;
  detail: string;
}

export interface PracticeExplanation {
  summary: string;
  parts: PracticeExplanationPart[];
  related: string;
}

export interface PracticeSnippet {
  id: string;
  language: 'react' | 'javascript' | 'html' | 'css' | 'dom';
  category: string;
  title: string;
  code: string;
  explanation: PracticeExplanation | null;
}

// One item of a session. A drill is a snippet plus how it is to be practiced,
// and the two modes carry different payloads: a blind drill gets the task
// description and deliberately no `code` — the server withholds the answer to
// the drill that exists to test memory, and it arrives only in the grade
// response. Modelled as a union rather than optional fields so a component
// cannot read `code` without first narrowing on the mode it exists in.
export type PracticeDrillMode = 'speed' | 'blind';

interface PracticeDrillBase {
  id: string;
  language: 'react' | 'javascript' | 'html' | 'css' | 'dom';
  category: string;
  title: string;
}

export interface PracticeSpeedDrill extends PracticeDrillBase {
  mode: 'speed';
  code: string;
  explanation: PracticeExplanation | null;
}

export interface PracticeBlindDrill extends PracticeDrillBase {
  mode: 'blind';
  prompt: string;
}

export type PracticeDrill = PracticeSpeedDrill | PracticeBlindDrill;

export interface PracticeRecallResult {
  verdict: 'correct' | 'partial' | 'wrong';
  passed: boolean;
  feedback: string;
  // 'fallback' means llama-server was unreachable and the verdict came from a
  // text comparison, not a reading of the code — the UI says so.
  gradedBy: 'model' | 'fallback' | 'empty';
  reference: string;
  // Withheld from the blind drill itself — naming every field of the snippet
  // gives most of the answer away — so it arrives here with the grade.
  explanation: PracticeExplanation | null;
  progress: PracticeSnippetProgress;
}

export interface PracticeSnippetWithProgress extends PracticeSnippet {
  progress: PracticeSnippetProgress | null;
}

export interface PracticeAttemptResult {
  rating: string;
  progress: PracticeSnippetProgress;
}

export interface PracticeLanguageStats {
  attempts: number;
  avgAccuracy: number | null;
  avgWpm: number | null;
}

export interface PracticeRecallStats {
  attempts: number;
  passes: number;
  // null with no attempts: "never asked to recall anything" is not 0%.
  passRate: number | null;
}

export interface PracticeStats {
  totalAttempts: number;
  avgAccuracy: number | null;
  avgWpm: number | null;
  byLanguage: Record<string, PracticeLanguageStats>;
  recall: PracticeRecallStats;
}

// --- fetch helpers ---

// A request to an unreachable backend (e.g. the Tailscale link is down in
// network mode) otherwise hangs until the OS TCP timeout — minutes — leaving
// React Query stuck `fetching` forever instead of falling back to the persisted
// cache. A hard client-side timeout turns that into a prompt failure. Reads are
// quick, so they get a tight bound; writes may hit slow AI endpoints, so theirs
// is generous.
const READ_TIMEOUT_MS = 20_000;
const WRITE_TIMEOUT_MS = 60_000;
// Uploads carry meeting audio and phone video over what may be a Tailscale
// link, so the bound is generous — but it has to exist. `upload` was the one
// helper with no timeout at all, and a half-open connection there hangs until
// the OS gives up, which now also stalls every queued recording behind it.
const UPLOAD_TIMEOUT_MS = 10 * 60_000;

/**
 * An error carrying the HTTP status, so a caller can tell "the server is
 * unhappy with this request" (4xx — retrying changes nothing) from "the server
 * or the network is having a moment" (5xx / no response — retry). The recording
 * queue needs that distinction: it must keep the audio either way, but only one
 * of the two is worth trying again.
 */
export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/**
 * The request never reached the backend — DNS, TCP, TLS, a dropped link, or a
 * timeout. Distinct from `ApiError`, which means the backend answered and said
 * no: that is a decision, and repeating the request will get the same one.
 *
 * The distinction is what the offline layer runs on. A write that fails this
 * way is retried into the paused queue and replayed later; a write the server
 * rejected is a real error the user has to see.
 */
export class NetworkError extends Error {
  /** True when we gave up waiting rather than being refused outright. An
   * overloaded backend can time out while being perfectly reachable, so this
   * one asks for a health probe instead of declaring the app offline. */
  readonly timedOut: boolean;
  constructor(message: string, timedOut: boolean) {
    super(message);
    this.name = 'NetworkError';
    this.timedOut = timedOut;
  }
}

async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs: number
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...init, signal: controller.signal });
    // Any answer at all — a 500 included — proves the backend is there. This
    // is the app's real reachability signal: it rides on traffic it was
    // already making, and it is never stale, which is what a polled health
    // check can only approximate.
    reportFetchOutcome('reachable');
    return response;
  } catch (error) {
    const timedOut = controller.signal.aborted;
    reportFetchOutcome(timedOut ? 'slow' : 'unreachable');
    throw new NetworkError(
      timedOut
        ? `Timed out after ${Math.round(timeoutMs / 1000)}s`
        : 'Could not reach the server',
      timedOut
    );
  } finally {
    clearTimeout(timer);
  }
}

async function get<T>(url: string): Promise<T> {
  const r = await fetchWithTimeout(
    url,
    { credentials: 'include' },
    READ_TIMEOUT_MS
  );
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
  const r = await fetchWithTimeout(
    url,
    {
      method,
      credentials: 'include',
      headers:
        body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    },
    WRITE_TIMEOUT_MS
  );
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

const upload = <T>(url: string, form: FormData) =>
  uploadWith<T>('POST', url, form);

async function uploadWith<T>(
  method: string,
  url: string,
  form: FormData
): Promise<T> {
  const r = await fetchWithTimeout(
    url,
    { method, credentials: 'include', body: form },
    UPLOAD_TIMEOUT_MS
  );
  if (!r.ok) {
    const b = await r.json().catch(() => ({}));
    throw new ApiError(b.error || `HTTP ${r.status}`, r.status);
  }
  return r.json();
}

async function uploadForBlob(url: string, form: FormData): Promise<Blob> {
  const r = await fetch(url, {
    method: 'POST',
    credentials: 'include',
    body: form,
  });
  if (!r.ok) {
    const b = await r.json().catch(() => ({}));
    throw new Error(b.error || `HTTP ${r.status}`);
  }
  return r.blob();
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
    updateAI: (
      data: Partial<
        AppSettings & {
          hfToken?: string;
          websearchSearchKey?: string;
          googleOauthClientId?: string;
          googleOauthClientSecret?: string;
          microsoftOauthClientId?: string;
          microsoftOauthClientSecret?: string;
          // Write-only, like the OAuth pair above: the GET returns
          // hasAdzunaCredentials rather than the key.
          adzunaAppId?: string;
          adzunaAppKey?: string;
        }
      >
    ) => patch<{ success: boolean }>('/api/settings/ai', data),
    updateShortcuts: (data: {
      sttPasteKey?: string;
      sttVoiceKey?: string;
      sttJournalKey?: string;
    }) => patch<{ success: boolean }>('/api/settings/ai', data),
    regenerateCode: () =>
      post<{ networkCode: string }>('/api/settings/regenerate-code'),
    llamaModels: () => get<LlamaModel[]>('/api/settings/llama-models'),
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
    get: (id: string) => get<JournalEntry>(`/api/journal/${id}`),
    create: (data: {
      content: string;
      title?: string;
      tags?: string[];
      // Optional client-supplied ULID so an offline-queued create replays
      // idempotently (server does INSERT OR IGNORE on this id).
      id?: string;
    }) => post<{ id: string }>('/api/journal', data),
    // Mirrors the STT_JOURNAL_KEY voice shortcut (stt/listener.py): save the
    // raw transcript immediately, polish it in the background.
    createFromVoice: (rawContent: string) =>
      post<{ id: string }>('/api/journal', { raw_content: rawContent }),
    // The bottom bar's Record button: keep the recording itself as the entry,
    // no speech-to-text. One request so a rejected upload leaves no empty entry
    // behind; the clip lands as the entry's first attachment.
    // `id`/`attachmentId` are client-minted ULIDs. The phone holds its audio
    // until the server confirms it landed and re-POSTs on every reconnect, so
    // these are what let the server recognise a replay instead of creating a
    // second entry and a second copy of the file.
    createRecording: (
      audio: Blob,
      opts: {
        id?: string;
        attachmentId?: string;
        name?: string;
        transcribe?: boolean;
      } = {}
    ) => {
      const form = new FormData();
      form.append('file', audio, recordingFilename(audio.type));
      if (opts.name?.trim()) form.append('name', opts.name.trim());
      if (opts.id) form.append('id', opts.id);
      if (opts.attachmentId) form.append('attachmentId', opts.attachmentId);
      if (opts.transcribe) form.append('transcribe', 'true');
      return upload<{ id: string; attachment: JournalAttachment }>(
        '/api/journal/recordings',
        form
      );
    },
    update: (
      id: string,
      data: { content?: string; title?: string; tags?: string[] }
    ) => patch<{ success: boolean }>(`/api/journal/${id}`, data),
    delete: (id: string) => del<{ success: boolean }>(`/api/journal/${id}`),
    polish: (id: string) =>
      post<{ success: boolean; content: string }>(`/api/journal/${id}/polish`),
    // Other entries from the same local day, for the voice-only merge picker.
    mergeCandidates: (id: string) =>
      get<JournalEntry[]>(`/api/journal/${id}/merge-candidates`),
    // Folds a voice-only entry's single recording into `targetId` and deletes
    // the now-empty source entry.
    merge: (id: string, targetId: string) =>
      post<JournalEntry>(`/api/journal/${id}/merge`, { targetId }),

    attachments: {
      list: (entryId: string) =>
        get<JournalAttachment[]>(`/api/journal/${entryId}/attachments`),
      upload: (entryId: string, file: File, name?: string) => {
        const form = new FormData();
        // Explicit filename: an iOS voice memo is a File with an empty `name`,
        // and the two-argument form would send `filename=""`.
        form.append('file', file, uploadFilenameFor(file));
        if (name?.trim()) form.append('name', name.trim());
        return upload<JournalAttachment>(
          `/api/journal/${entryId}/attachments`,
          form
        );
      },
      rename: (attachmentId: string, name: string) =>
        patch<JournalAttachment>(`/api/journal/attachments/${attachmentId}`, {
          name,
        }),
      delete: (attachmentId: string) =>
        del<{ success: boolean }>(`/api/journal/attachments/${attachmentId}`),
      // Opt-in per attachment: transcribes audio, captions an image. Returns
      // as soon as the work is queued; the result arrives over /api/journal/events.
      transcribe: (attachmentId: string) =>
        post<JournalAttachment>(
          `/api/journal/attachments/${attachmentId}/transcribe`
        ),
      // Separate from transcribe: describes non-speech audio content
      // (audio/video only) via a different, audio-capable model.
      describeAudio: (attachmentId: string) =>
        post<JournalAttachment>(
          `/api/journal/attachments/${attachmentId}/describe-audio`
        ),
    },

    // Voice drafts (backend/journal/voice_drafts.py): clips from the STT
    // listener's Journal hotkey, saved for slow multi-model transcription
    // instead of being turned into text on the spot. Creation happens on the
    // listener's Python side, not here — these cover the dropdown's needs.
    voiceDrafts: {
      list: () => get<JournalVoiceDraft[]>('/api/journal/voice-drafts'),
      delete: (id: string) =>
        del<{ success: boolean }>(`/api/journal/voice-drafts/${id}`),
      retry: (id: string) =>
        post<{ success: boolean }>(`/api/journal/voice-drafts/${id}/retry`),
    },
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
    // Create a recipe from title/content + optional photos/videos (one multipart POST).
    create: (data: {
      title: string;
      content: string;
      tags?: string[];
      media?: Blob[];
      // Client-minted ids — see api.food.create. A recipe queued offline
      // replays under the same id, photos included.
      id?: string;
      mediaIds?: string[];
    }) => {
      const form = new FormData();
      if (data.id) form.set('id', data.id);
      if (data.mediaIds?.length)
        form.set('mediaIds', JSON.stringify(data.mediaIds));
      form.set('title', data.title);
      form.set('content', data.content);
      if (data.tags) form.set('tags', JSON.stringify(data.tags));
      data.media?.forEach((f, i) =>
        form.append(
          'media',
          f,
          f instanceof File ? f.name : `${data.mediaIds?.[i] ?? 'photo'}.jpg`
        )
      );
      return upload<Recipe>('/api/cookbook', form);
    },
    update: (
      id: string,
      data: { title?: string; content?: string; tags?: string[] }
    ) => patch<{ success: boolean }>(`/api/cookbook/${id}`, data),
    delete: (id: string) => del<{ success: boolean }>(`/api/cookbook/${id}`),
    importRecipe: (data: { text?: string; url?: string }) =>
      post<{ id: string; recipe: Recipe }>('/api/cookbook/import', data),
    generate: (prompt: string) =>
      post<{ id: string; recipe: Recipe }>('/api/cookbook/generate', {
        prompt,
      }),
    addMedia: (id: string, media: Blob[], mediaIds?: string[]) => {
      const form = new FormData();
      // Ids, when the caller has them: this is also the path a photo takes when
      // its recipe was already created and only the picture is still queued.
      if (mediaIds?.length) form.set('mediaIds', JSON.stringify(mediaIds));
      media.forEach((f, i) =>
        form.append(
          'media',
          f,
          f instanceof File ? f.name : `${mediaIds?.[i] ?? 'photo'}.jpg`
        )
      );
      return upload<{ media: RecipeMedia[] }>(
        `/api/cookbook/${id}/media`,
        form
      );
    },
    deleteMedia: (mediaId: string) =>
      del<{ success: boolean }>(`/api/cookbook/media/${mediaId}`),
  },

  food: {
    list: (params?: { limit?: number; offset?: number; tag?: string }) => {
      const qp = new URLSearchParams();
      if (params?.limit !== undefined) qp.set('limit', String(params.limit));
      if (params?.offset !== undefined) qp.set('offset', String(params.offset));
      if (params?.tag) qp.set('tag', params.tag);
      return get<FoodEntry[]>(`/api/food?${qp}`);
    },
    journal: () => get<FoodJournalItem[]>('/api/food/journal'),
    tags: () => get<RecipeTag[]>('/api/food/tags'),
    get: (id: string) => get<FoodEntry>(`/api/food/${id}`),
    // Create an entry from raw text + optional photos/videos (one multipart POST).
    create: (data: {
      text?: string;
      dish?: string;
      place?: string;
      notes?: string;
      rating?: number;
      tags?: string[];
      media?: Blob[];
      latitude?: number;
      longitude?: number;
      // Client-minted ids: the entry's, and one per photo, positionally. They
      // are what let a queued offline capture be replayed without producing a
      // second meal or a second copy of the picture.
      id?: string;
      mediaIds?: string[];
    }) => {
      const form = new FormData();
      if (data.id) form.set('id', data.id);
      if (data.mediaIds?.length)
        form.set('mediaIds', JSON.stringify(data.mediaIds));
      if (data.text) form.set('text', data.text);
      if (data.dish) form.set('dish', data.dish);
      if (data.place) form.set('place', data.place);
      if (data.notes) form.set('notes', data.notes);
      if (data.rating !== undefined) form.set('rating', String(data.rating));
      if (data.tags) form.set('tags', JSON.stringify(data.tags));
      if (data.latitude !== undefined)
        form.set('latitude', String(data.latitude));
      if (data.longitude !== undefined)
        form.set('longitude', String(data.longitude));
      // A queued photo comes back from IndexedDB as a Blob, not a File, and
      // FormData would then name it "blob" — the server resolves an upload's
      // extension from its filename when the mime type is unhelpful, so it
      // gets a real one.
      data.media?.forEach((f, i) =>
        form.append(
          'media',
          f,
          f instanceof File ? f.name : `${data.mediaIds?.[i] ?? 'photo'}.jpg`
        )
      );
      return upload<FoodEntry>('/api/food', form);
    },
    update: (
      id: string,
      data: {
        dish?: string | null;
        place?: string | null;
        notes?: string | null;
        rating?: number | null;
        tags?: string[];
        recipeId?: string | null;
      }
    ) => patch<{ success: boolean }>(`/api/food/${id}`, data),
    delete: (id: string) => del<{ success: boolean }>(`/api/food/${id}`),
    addMedia: (id: string, media: File[]) => {
      const form = new FormData();
      for (const f of media) form.append('media', f);
      return upload<{ media: FoodMedia[] }>(`/api/food/${id}/media`, form);
    },
    deleteMedia: (mediaId: string) =>
      del<{ success: boolean }>(`/api/food/media/${mediaId}`),
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
    // `deep` asks for the slow pass that re-reads every saved chapter and
    // rewrites the ones the author edited since we downloaded them.
    checkUpdates: (ficId: string, deep = false) =>
      post<{ id: string; queued: boolean; deep: boolean }>(
        `/api/fanfic/${ficId}/check-updates`,
        deep ? { deep: true } : undefined
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
    bookmarks: {
      list: (ficId: string) =>
        get<FicBookmark[]>(`/api/fanfic/${ficId}/bookmarks`),
      create: (
        ficId: string,
        data: {
          chapterId: string;
          type: 'favorite' | 'continue';
          scrollPosition: number;
        }
      ) => post<FicBookmark>(`/api/fanfic/${ficId}/bookmarks`, data),
      delete: (bookmarkId: string) =>
        del<{ success: boolean }>(`/api/fanfic/bookmarks/${bookmarkId}`),
    },
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
    scanWatched: (domain: string) =>
      post<{ started: boolean }>(`/api/fanfic/scan-watched/${domain}`),
  },

  calendar: {
    listByRange: (start: string, end: string) =>
      get<CalendarEvent[]>(`/api/calendar?start=${start}&end=${end}`),
    listByDate: (date: string) =>
      get<CalendarEvent[]>(`/api/calendar/date/${date}`),
    listByWeek: (date: string) =>
      get<CalendarEvent[]>(`/api/calendar/week/${date}`),
    // The whole table's tag vocabulary, not just the month on screen — same
    // {name, count} shape as the cookbook and food-log pill rows.
    tags: () => get<RecipeTag[]>('/api/calendar/tags'),
    get: (id: string) => get<CalendarEvent>(`/api/calendar/${id}`),
    findRelatedJournals: (date: string) =>
      get<JournalEntry[]>(`/api/calendar/related-journals/${date}`),
    create: (
      data: {
        title: string;
        date: string;
        description?: string;
        time?: string;
        endTime?: string;
        allDay?: boolean;
        tags?: string[];
        journalId?: string;
        // Grouping categories set by hand at creation. Sending the key at all
        // stamps `classified_at`, which retires the event from the AI
        // classifier — so the form omits it unless something is checked.
        categoryTags?: string[];
        // Optional client-supplied ULID so an offline-queued create replays
        // idempotently (server does INSERT OR IGNORE on this id).
        id?: string;
      } & CalendarRepeat
    ) => post<{ id: string }>('/api/calendar', data),
    // Edits/erases every occurrence, past ones included.
    update: (id: string, data: Record<string, unknown>) =>
      patch<{ success: boolean }>(`/api/calendar/${id}`, data),
    delete: (id: string) => del<{ success: boolean }>(`/api/calendar/${id}`),
    // "This and future": what already happened is left exactly as it was.
    // endSeries caps the rule the day before; updateFrom splits the series,
    // returning the new row's id (or the original when nothing was split).
    endSeries: (id: string, date: string) =>
      del<{ success: boolean; deleted: boolean }>(
        `/api/calendar/${id}/from/${date}`
      ),
    updateFrom: (id: string, date: string, data: Record<string, unknown>) =>
      patch<{ id: string; split: boolean }>(
        `/api/calendar/${id}/from/${date}`,
        data
      ),
    // Drop or reschedule a single occurrence, leaving the rest of the series.
    skipOccurrence: (id: string, date: string) =>
      del<{ success: boolean }>(`/api/calendar/${id}/occurrence/${date}`),
    moveOccurrence: (
      id: string,
      date: string,
      data: { newDate?: string; newTime?: string; newEndTime?: string }
    ) =>
      patch<{ success: boolean }>(
        `/api/calendar/${id}/occurrence/${date}`,
        data
      ),
    linkJournal: (id: string, journalEntryId: string) =>
      post<{ id: string }>(`/api/calendar/${id}/link`, { journalEntryId }),
    unlinkJournal: (id: string, journalEntryId: string) =>
      del<{ success: boolean }>(`/api/calendar/${id}/link/${journalEntryId}`),
    // Saves an already-transcribed recording as the event's description and
    // queues AI category classification — no confirm step. `text` comes from
    // /api/transcribe, run client-side first (same as every other useRecorder
    // caller). Returns the updated event immediately; categoryTags/
    // classifiedAt land once the background classification finishes.
    transcribe: (id: string, text: string) =>
      post<CalendarEvent>(`/api/calendar/${id}/transcribe`, { text }),
    // Wake/sleep for a day. Derived from when the user was active unless they
    // set it by hand; `set` takes the whole manual state, so omitting an end
    // hands it back to the derived value, and `clear` releases both.
    sleep: {
      get: (date: string) => get<SleepDay>(`/api/calendar/sleep/${date}`),
      set: (date: string, times: { wake?: string; sleep?: string }) =>
        put<SleepDay>(`/api/calendar/sleep/${date}`, times),
      clear: (date: string) => del<SleepDay>(`/api/calendar/sleep/${date}`),
    },
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

    listAttempts: (params?: { tag?: string; folderId?: string }) => {
      const qp = new URLSearchParams();
      if (params?.tag) qp.set('tag', params.tag);
      if (params?.folderId) qp.set('folderId', params.folderId);
      return get<LearningAttempt[]>(`/api/learning/attempts?${qp}`);
    },
    // Saves an answered/flipped card so the session survives leaving the view.
    // Returns immediately; the AI grade lands on the row in the background and
    // shows up on the next listAttempts.
    saveAttempt: (data: {
      // Client-supplied ULID, so an offline-queued save replays idempotently.
      id: string;
      cardId: string;
      mode: 'answered' | 'skipped';
      answer?: string;
      answerMode?: 'typed' | 'voice';
      // Speech mode's toggle state at submit time — see ReviewSession.
      speechMode?: boolean;
    }) =>
      post<{ success: boolean; id: string }>('/api/learning/attempts', data),
    review: (
      id: string,
      data: {
        rating: number;
        suggestedRating?: number;
        userAnswer?: string;
        coverage?: ClaimCoverage;
        answerMode?: 'typed' | 'voice' | 'self';
        // Optional client-supplied ULID so an offline-queued review replays
        // idempotently (server skips re-applying FSRS if it already exists).
        reviewId?: string;
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
    generateFromNote: (content: string) =>
      post<{
        count: number;
        ids: string[];
        cards: DraftCard[];
        folderId: string;
      }>('/api/learning/generate-from-note', { content }),

    listQueue: () => get<LearningCard[]>('/api/learning/queue'),
    approve: (id: string, force?: boolean) =>
      post<ApproveResult>(`/api/learning/queue/${id}/approve`, { force }),
    regenerate: (id: string, direction: string) =>
      post<{ count: number; ids: string[]; cards: DraftCard[] }>(
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
    today: () =>
      get<ConversationWithMessages | null>('/api/chat/today?mode=chat'),
    journalConversations: () =>
      get<DatedConversation[]>('/api/chat/journal-conversations'),
    generateTitle: (id: string) =>
      post<{ title: string | null }>(
        `/api/chat/conversations/${id}/generate-title`,
        {}
      ),
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
    // `rawContent` is the verbatim transcript when the message was dictated;
    // `attachmentIds` claims the photos staged before this message existed.
    addMessage: (
      id: string,
      data: {
        role: string;
        content: string;
        metadata?: string;
        rawContent?: string | null;
        attachmentIds?: string[];
      }
    ) => post<{ id: string }>(`/api/chat/conversations/${id}/messages`, data),
    // `coords` is the device's position, kept as a fallback for the photo's own
    // EXIF GPS — iOS strips that whenever an image goes through the clipboard or
    // a share sheet, which is exactly what paste and drop produce.
    uploadAttachments: (
      conversationId: string,
      files: File[],
      coords?: { latitude: number; longitude: number } | null
    ) => {
      const form = new FormData();
      for (const file of files) {
        form.append('image', file, uploadFilenameFor(file));
      }
      if (coords) {
        form.append('latitude', String(coords.latitude));
        form.append('longitude', String(coords.longitude));
      }
      return upload<ChatAttachment[]>(
        `/api/chat/conversations/${conversationId}/attachments`,
        form
      );
    },
    getAttachment: (id: string) =>
      get<ChatAttachment>(`/api/chat/attachments/${id}`),
    deleteAttachment: (id: string) =>
      del<{ success: boolean }>(`/api/chat/attachments/${id}`),
    runBriefing: () =>
      post<{
        conversationId: string;
        messageId: string;
        briefing: string;
        todosAdded: number;
      }>('/api/chat/briefing/run', {}),
    // `data` carries the card's edited values on accept — the card is a form,
    // so what gets written is what the user is looking at, not what the model
    // first staged. Omitted on dismiss, and on an accept with no edits.
    resolveProposal: (
      messageId: string,
      proposalId: string,
      action: 'accept' | 'dismiss',
      data?: Record<string, unknown>
    ) =>
      post<{ proposal: DelegateProposalRecord }>(
        `/api/chat/proposals/${messageId}/${proposalId}`,
        data ? { action, data } : { action }
      ),
  },

  // The standing document about the user that rides in every chat system
  // prompt. These routes are its only write path — chat used to edit it itself,
  // with no confirm card, and no longer can.
  memory: {
    get: () => get<UserMemory>('/api/memory'),
    update: (content: string) => put<UserMemory>('/api/memory', { content }),
    revisions: () => get<MemoryRevision[]>('/api/memory/revisions'),
    restore: (id: string) =>
      post<UserMemory>(`/api/memory/revisions/${id}/restore`, {}),
  },

  // Notes to self: created only from chat (backend/delegate/tools.py's
  // create_note_to_self), no create route here — same shape as memory above.
  notes: {
    due: () => get<NoteToSelf[]>('/api/notes/due'),
    dismiss: (id: string) => post<NoteToSelf>(`/api/notes/${id}/dismiss`, {}),
    update: (id: string, content: string) =>
      put<NoteToSelf>(`/api/notes/${id}`, { content }),
    revisions: (id: string) =>
      get<NoteToSelfRevision[]>(`/api/notes/${id}/revisions`),
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
    upload: (path: string, files: File[]) => {
      const form = new FormData();
      form.append('path', path);
      for (const file of files) form.append('file', file);
      return upload<FileUploadResult>('/api/files/upload', form);
    },
    /** Not fetched — pointed at directly as an <img>/<video>/<a> src or href. */
    contentUrl: (path: string, download = false) =>
      `/api/files/content?path=${encodeURIComponent(path)}${download ? '&download=1' : ''}`,
    getConfig: () => get<FilesConfig>('/api/files/config'),
    setConfig: (destination: string) =>
      put<FilesConfig>('/api/files/config', { destination }),
  },

  notebook: {
    files: {
      list: (path?: string) =>
        get<FileEntry[]>(
          `/api/notebook/files?${path ? `path=${encodeURIComponent(path)}` : ''}`
        ),
      /** Every note in the notebook, walked recursively — what the index page's
       * generated tree and `:find` are both built from. `list` only sees one
       * directory, so both would otherwise need a request per folder. */
      tree: () =>
        get<{ entries: FileEntry[]; truncated: boolean }>(
          '/api/notebook/files/tree'
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
      /** Reads `path`, silently creating it as an empty note first if it
       * doesn't exist yet — shared by Notebook's auto-open-index-on-mount,
       * diary-jump, link-follow, and :q-goes-home flows. */
      ensure: async (path: string): Promise<void> => {
        try {
          await get<{ content: string }>(
            `/api/notebook/files/read?path=${encodeURIComponent(path)}`
          );
        } catch {
          await post<{ success: boolean }>('/api/notebook/files/write', {
            path,
            content: '',
          });
        }
      },
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
    transcribe: (audio: Blob) => {
      const form = new FormData();
      form.append('audio', audio, recordingFilename(audio.type));
      return upload<{ text?: string }>('/api/transcribe', form);
    },
    reload: () => post<{ success: boolean }>('/api/stt/reload'),
    listenerState: () =>
      get<{ recording: boolean; transcribing: boolean; mode: string | null }>(
        '/api/stt/listener-state'
      ),
  },

  repos: {
    list: () => get<Repo[]>('/api/repos'),
    get: (id: string) => get<Repo>(`/api/repos/${id}`),
    create: (data: { remoteUrl: string; name?: string; branch?: string }) =>
      post<Repo & { queued: boolean }>('/api/repos', data),
    // Fetch + hard reset + graph refresh, on the research worker. 202 with
    // {queued}, or 409 when the worker already has a job.
    pull: (id: string) =>
      post<{ queued: boolean }>(`/api/repos/${id}/pull`, {}),
    makeDefault: (id: string) =>
      post<{ success: boolean }>(`/api/repos/${id}/default`, {}),
    remove: (id: string) => del<{ success: boolean }>(`/api/repos/${id}`),
  },

  ideas: {
    list: () => get<IdeaSummary[]>('/api/ideas'),
    get: (id: string) => get<Idea>(`/api/ideas/${id}`),
    create: (data: {
      title?: string;
      rawContent?: string;
      tags?: string[];
      // Omit to let the server stamp the default repo; '' detaches.
      repoId?: string;
      // Optional client-supplied ULID — see api.journal.create.
      id?: string;
    }) => post<{ id: string }>('/api/ideas', data),
    createFromVoice: (rawContent: string, id?: string, repoId?: string) =>
      post<{ id: string }>('/api/ideas/voice', { rawContent, id, repoId }),
    update: (
      id: string,
      data: {
        title?: string;
        rawContent?: string;
        content?: string;
        status?: IdeaStatus;
        tags?: string[];
        userVerdict?: IdeaVerdict | null;
        userVerdictNote?: string;
        repoId?: string | null;
      }
    ) => patch<{ success: boolean }>(`/api/ideas/${id}`, data),
    remove: (id: string) => del<{ success: boolean }>(`/api/ideas/${id}`),

    listSketches: (ideaId: string) =>
      get<IdeaSketch[]>(`/api/ideas/${ideaId}/sketches`),
    addSketch: (ideaId: string, data: { pageId: string; caption?: string }) =>
      post<{ id: string }>(`/api/ideas/${ideaId}/sketches`, data),
    updateSketch: (
      sketchId: string,
      data: { caption?: string; position?: number }
    ) => patch<{ success: boolean }>(`/api/ideas/sketches/${sketchId}`, data),
    removeSketch: (sketchId: string) =>
      del<{ success: boolean }>(`/api/ideas/sketches/${sketchId}`),
    paperPages: () => get<IdeaPaperPage[]>('/api/ideas/paper-pages'),

    assess: (ideaId: string) =>
      post<IdeaAssessment>(`/api/ideas/${ideaId}/assess`),
    listQuestions: (ideaId: string) =>
      get<IdeaQuestion[]>(`/api/ideas/${ideaId}/questions`),
    answerQuestion: (
      questionId: string,
      data: { answer?: string; status?: 'open' | 'answered' | 'dismissed' }
    ) =>
      patch<{ success: boolean }>(`/api/ideas/questions/${questionId}`, data),

    listConversations: (ideaId: string) =>
      get<Conversation[]>(`/api/ideas/${ideaId}/conversations`),
    createConversation: (ideaId: string, data: { title?: string } = {}) =>
      post<{ id: string }>(`/api/ideas/${ideaId}/conversations`, data),

    listPlans: (ideaId: string) =>
      get<IdeaPlanSummary[]>(`/api/ideas/${ideaId}/plans`),
    getPlan: (planId: string) => get<IdeaPlan>(`/api/ideas/plans/${planId}`),
    createPlan: (ideaId: string) => post<IdeaPlan>(`/api/ideas/${ideaId}/plan`),

    repoContext: () => get<RepoSnapshot | null>('/api/ideas/repo-context'),
    researchStatus: () => get<ResearchStatus>('/api/ideas/research/status'),
    cancelResearch: () =>
      post<{ cancelled: boolean }>('/api/ideas/research/cancel'),
    research: (ideaId: string) =>
      post<{ queued: boolean }>(`/api/ideas/${ideaId}/research`),
    refreshRepoContext: () =>
      post<{ id: string; routeCount: number; tableCount: number }>(
        '/api/ideas/repo-context/refresh'
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
    events: () => get<TaskEvent[]>('/api/tasks/events'),
  },

  todos: {
    list: () => get<TodoItem[]>('/api/tasks/todos'),
    create: (data: TodoPayload & { title: string; id?: string }) =>
      post<{ id: string }>('/api/tasks/todos', data),
    update: (id: string, data: TodoPayload) =>
      patch<{ success: boolean }>(`/api/tasks/todos/${id}`, data),
    remove: (id: string) => del<{ success: boolean }>(`/api/tasks/todos/${id}`),
  },

  // Today's chat to-dos (see ChatTodoItem above) — the Chat tab's bar reads
  // and edits these; `promote` moves one into the permanent list above.
  chatTodos: {
    list: () => get<ChatTodoItem[]>('/api/tasks/chat-todos'),
    add: (items: { title: string; notes?: string }[]) =>
      post<ChatTodoItem[]>('/api/tasks/chat-todos', { items }),
    update: (id: string, data: ChatTodoPayload) =>
      patch<{ success: boolean }>(`/api/tasks/chat-todos/${id}`, data),
    remove: (id: string) =>
      del<{ success: boolean }>(`/api/tasks/chat-todos/${id}`),
    promote: (id: string, data: TodoPayload & { title: string }) =>
      post<{ id: string }>(`/api/tasks/chat-todos/${id}/promote`, data),
  },

  newspapers: {
    getByDate: (date: string) =>
      get<FrontPage[]>(`/api/newspapers/frontpages/${date}`),
    sync: () => post<SyncResult[]>('/api/newspapers/sync'),
  },

  email: {
    accounts: () => get<EmailAccountStatus[]>('/api/email/accounts'),
    oauthStatus: (provider: EmailProvider = 'gmail') =>
      get<EmailOauthStatus>(`/api/email/oauth/status?provider=${provider}`),
    disconnect: (provider: EmailProvider = 'gmail') =>
      post<{ success: boolean }>(
        `/api/email/oauth/disconnect?provider=${provider}`
      ),
    connectImap: (body: {
      host: string;
      port: number;
      username: string;
      password: string;
      emailAddress: string;
    }) =>
      post<{ success: boolean } | { error: string }>(
        '/api/email/imap/connect',
        body
      ),
    /** No provider: syncs every connected account, returned as
     *  {accountId: result}. With a provider: syncs just that one account,
     *  returned as a single result. */
    syncNow: (provider?: EmailProvider) =>
      post<Record<string, EmailSyncResult> | EmailSyncResult>(
        `/api/email/sync${provider ? `?provider=${provider}` : ''}`
      ),
    list: (
      params: {
        category?: EmailCategory;
        jobStatus?: JobApplicationStatus;
        query?: string;
        limit?: number;
        offset?: number;
      } = {}
    ) => {
      const q = new URLSearchParams();
      if (params.category) q.set('category', params.category);
      if (params.jobStatus) q.set('jobStatus', params.jobStatus);
      if (params.query) q.set('query', params.query);
      if (params.limit != null) q.set('limit', String(params.limit));
      if (params.offset != null) q.set('offset', String(params.offset));
      const qs = q.toString();
      return get<EmailMessage[]>(`/api/email${qs ? `?${qs}` : ''}`);
    },
    get: (id: string) => get<EmailMessage>(`/api/email/${id}`),
    stats: () => get<EmailStats>('/api/email/stats'),
    imageStatus: () => get<EmailImageStatus>('/api/email/image-status'),
  },

  practice: {
    session: (
      params: { language?: string; category?: string; size?: number } = {}
    ) => {
      const qp = new URLSearchParams();
      if (params.language) qp.set('language', params.language);
      if (params.category) qp.set('category', params.category);
      if (params.size !== undefined) qp.set('size', String(params.size));
      const qs = qp.toString();
      return get<PracticeDrill[]>(`/api/practice/session${qs ? `?${qs}` : ''}`);
    },
    listSnippets: (params: { language?: string; category?: string } = {}) => {
      const qp = new URLSearchParams();
      if (params.language) qp.set('language', params.language);
      if (params.category) qp.set('category', params.category);
      const qs = qp.toString();
      return get<PracticeSnippetWithProgress[]>(
        `/api/practice/snippets${qs ? `?${qs}` : ''}`
      );
    },
    submitAttempt: (body: {
      snippetId: string;
      wpm: number;
      accuracy: number;
      errorCount: number;
    }) => post<PracticeAttemptResult>('/api/practice/attempts', body),
    gradeRecall: (body: { snippetId: string; submitted: string }) =>
      post<PracticeRecallResult>('/api/practice/recall', body),
    stats: () => get<PracticeStats>('/api/practice/stats'),
  },

  paper: {
    list: (params: { limit?: number; offset?: number } = {}) => {
      const q = new URLSearchParams();
      if (params.limit != null) q.set('limit', String(params.limit));
      if (params.offset != null) q.set('offset', String(params.offset));
      const qs = q.toString();
      return get<PaperDoc[]>(`/api/paper${qs ? `?${qs}` : ''}`);
    },
    get: (id: string) => get<PaperDetail>(`/api/paper/${id}`),
    journal: () => get<JournalPaper[]>('/api/paper/journal'),
    // Both ids are the client's: a paper started with no backend in reach has
    // to have an identity before the server can give it one.
    create: (data?: { id: string; pageId: string; title?: string }) =>
      post<{ id: string; pageId: string }>('/api/paper', data),
    updateTitle: (id: string, title: string) =>
      patch<{ success: boolean }>(`/api/paper/${id}`, { title }),
    setArchiveRequested: (id: string, archiveRequested: boolean) =>
      patch<{ success: boolean }>(`/api/paper/${id}`, { archiveRequested }),
    remove: (id: string) => del<{ success: boolean }>(`/api/paper/${id}`),
    // The page's id is the client's too: a fresh page has to exist on the
    // tablet before the server hears about it.
    addPage: (id: string, pageId?: string) =>
      post<{ id: string; position: number }>(`/api/paper/${id}/pages`, {
        id: pageId,
      }),
    getPage: (pageId: string) =>
      get<PaperPageContent>(`/api/paper/pages/${pageId}`),
    addImage: (
      pageId: string,
      file: Blob,
      box: { x: number; y: number; width: number; height: number },
      filename = 'pasted.png',
      // Client-minted, so a picture pasted offline can be queued and replayed
      // without pasting itself twice.
      id?: string
    ) => {
      const form = new FormData();
      form.set('image', file, filename);
      form.set('x', String(box.x));
      form.set('y', String(box.y));
      form.set('width', String(box.width));
      form.set('height', String(box.height));
      if (id) form.set('id', id);
      return upload<PaperPageImage>(`/api/paper/pages/${pageId}/images`, form);
    },
    updateImage: (
      imageId: string,
      data: Partial<{
        x: number;
        y: number;
        width: number;
        height: number;
        rotation: number;
        flipped: boolean;
        locked: boolean;
      }>
    ) => patch<PaperPageImage>(`/api/paper/images/${imageId}`, data),
    deleteImage: (imageId: string) =>
      del<{ success: boolean }>(`/api/paper/images/${imageId}`),
    savePage: (
      pageId: string,
      data: { strokes: string; width: number; height: number; snapshot: Blob }
    ) => {
      const form = new FormData();
      // Strokes travel as a *file* part, not a text field: Werkzeug caps
      // non-file form fields at max_form_memory_size (500kB by default) and a
      // densely written page can exceed that, which the server rejects with a
      // 413 before the route ever runs.
      form.set(
        'strokes',
        new Blob([data.strokes], { type: 'application/json' }),
        'strokes.json'
      );
      form.set('width', String(data.width));
      form.set('height', String(data.height));
      form.set('snapshot', data.snapshot, 'snapshot.png');
      // Through the shared upload helper rather than a bare fetch: that is what
      // gives it a timeout, a typed NetworkError, and — the reason it matters
      // here — a reachability report, so a page that fails to save is what
      // tells the app it is offline.
      return uploadWith<{ success: boolean }>(
        'PUT',
        `/api/paper/pages/${pageId}`,
        form
      );
    },
    removePage: (pageId: string) =>
      del<{ success: boolean }>(`/api/paper/pages/${pageId}`),
  },

  lifestyle: {
    workouts: {
      list: (params?: { limit?: number; offset?: number }) => {
        const qp = new URLSearchParams();
        if (params?.limit !== undefined) qp.set('limit', String(params.limit));
        if (params?.offset !== undefined)
          qp.set('offset', String(params.offset));
        return get<WorkoutSession[]>(`/api/lifestyle/workouts?${qp}`);
      },
      get: (id: string) => get<WorkoutSession>(`/api/lifestyle/workouts/${id}`),
      create: (data: {
        date?: string;
        locationType: ActivityTypeId;
        durationMinutes?: number | null;
        /** 1-5 stars; the backend rejects anything outside that. */
        intensityRating?: number | null;
        rawText?: string;
        notes?: string;
      }) => post<WorkoutSession>('/api/lifestyle/workouts', data),
      update: (
        id: string,
        data: {
          date?: string;
          locationType?: ActivityTypeId;
          durationMinutes?: number | null;
          /** 1-5 stars; the backend rejects anything outside that. */
          intensityRating?: number | null;
          rawText?: string;
          notes?: string;
        }
      ) => patch<{ success: boolean }>(`/api/lifestyle/workouts/${id}`, data),
      // Re-run the AI parse over the raw text, which is never overwritten.
      reparse: (id: string) =>
        post<{ success: boolean }>(`/api/lifestyle/workouts/${id}/reparse`),
      delete: (id: string) =>
        del<{ success: boolean }>(`/api/lifestyle/workouts/${id}`),
    },
    heatmap: (params?: { start?: string; end?: string }) => {
      const qp = new URLSearchParams();
      if (params?.start) qp.set('start', params.start);
      if (params?.end) qp.set('end', params.end);
      return get<HeatmapDayResponse[]>(`/api/lifestyle/heatmap?${qp}`);
    },
    trends: (weeks?: number) =>
      get<{ weeks: TrendWeek[] }>(
        `/api/lifestyle/trends${weeks ? `?weeks=${weeks}` : ''}`
      ),
    exercises: {
      list: () => get<ExerciseSummary[]>('/api/lifestyle/exercises'),
      progression: (name: string) =>
        get<ExerciseProgression>(
          `/api/lifestyle/exercises/${encodeURIComponent(name)}/progression`
        ),
      merge: (from: string, into: string) =>
        post<{ success: boolean; moved: number }>(
          '/api/lifestyle/exercises/merge',
          { from, into }
        ),
    },
    weight: {
      list: (params?: { start?: string; end?: string }) => {
        const qp = new URLSearchParams();
        if (params?.start) qp.set('start', params.start);
        if (params?.end) qp.set('end', params.end);
        return get<BodyWeightLog[]>(`/api/lifestyle/weight?${qp}`);
      },
      log: (data: { date?: string; weight: number }) =>
        post<BodyWeightLog>('/api/lifestyle/weight', data),
      delete: (id: string) =>
        del<{ success: boolean }>(`/api/lifestyle/weight/${id}`),
    },
    selfies: {
      list: (limit?: number) =>
        get<Selfie[]>(
          `/api/lifestyle/selfies${limit ? `?limit=${limit}` : ''}`
        ),
      upload: (image: Blob, date?: string, filename?: string) => {
        const form = new FormData();
        // A queued photo comes back from IndexedDB as a Blob, which has no
        // name of its own — so the picked file's name is carried alongside it.
        // The route resolves the stored extension from the mime type first and
        // the filename second, and a camera roll HEIC arriving as
        // `application/octet-stream` is exactly the case where the second one
        // decides whether it gets transcoded or written as a .jpg that is not
        // a JPEG.
        form.set('image', image, filename || 'selfie.jpg');
        if (date) form.set('date', date);
        return upload<Selfie>('/api/lifestyle/selfies', form);
      },
      delete: (id: string) =>
        del<{ success: boolean }>(`/api/lifestyle/selfies/${id}`),
    },
    calories: {
      day: (date?: string) =>
        get<CalorieDay>(
          `/api/lifestyle/calories${date ? `?date=${date}` : ''}`
        ),
      create: (data: {
        date?: string;
        description: string;
        calories: number;
        // Optional client-supplied ULID — see api.journal.create.
        id?: string;
      }) => post<CalorieLog>('/api/lifestyle/calories', data),
      delete: (id: string) =>
        del<{ success: boolean }>(`/api/lifestyle/calories/${id}`),
    },
    weather: {
      today: () => get<WeatherToday>('/api/lifestyle/weather/today'),
      updateLocation: (latitude: number, longitude: number) =>
        post<WeatherToday>('/api/lifestyle/weather/location', {
          latitude,
          longitude,
        }),
    },
  },

  backup: {
    status: () => get<BackupStatus>('/api/backup/status'),
    run: () => post<BackupRun>('/api/backup/run', {}),
    getConfig: () => get<BackupConfig>('/api/backup/config'),
    setConfig: (data: { destination?: string; retentionDays?: number }) =>
      put<BackupConfig>('/api/backup/config', data),
    browse: (path: string) =>
      get<BackupBrowse>(`/api/backup/browse?path=${encodeURIComponent(path)}`),
  },

  logs: {
    units: () => get<ServerLogUnit[]>('/api/logs/units'),
    get: (p: {
      unit: string;
      lines: number;
      since: string;
      priority?: number;
    }) => {
      const q = new URLSearchParams({
        unit: p.unit,
        lines: String(p.lines),
        since: p.since,
      });
      if (p.priority !== undefined) q.set('priority', String(p.priority));
      return get<ServerLogResponse>(`/api/logs?${q}`);
    },
  },

  tts: {
    // Existing /api/tts endpoint (backend/routes/stt.py), previously only
    // consumed by the standalone STT listener; this is the first browser caller.
    speak: (text: string) => {
      const form = new FormData();
      form.set('text', text);
      return uploadForBlob('/api/tts', form);
    },
  },

  jobs: {
    profile: {
      get: () => get<JobProfileBundle>('/api/jobs/profile'),
      update: (data: Partial<JobProfileContact>) =>
        patch<JobProfileBundle>('/api/jobs/profile', data),
      create: (kind: ProfileSection, data: Record<string, unknown>) =>
        post<{ id: string }>(`/api/jobs/profile/${kind}`, data),
      update_: (
        kind: ProfileSection,
        id: string,
        data: Record<string, unknown>
      ) => patch<{ success: boolean }>(`/api/jobs/profile/${kind}/${id}`, data),
      remove: (kind: ProfileSection, id: string) =>
        del<{ success: boolean }>(`/api/jobs/profile/${kind}/${id}`),

      /** Read a resume into profile shape. Writes nothing — review first. */
      importFile: (file: File) => {
        const form = new FormData();
        form.append('file', file);
        return upload<ResumeImportPreview>('/api/jobs/profile/import', form);
      },
      importText: (text: string) =>
        post<ResumeImportPreview>('/api/jobs/profile/import', { text }),
      /** Write the reviewed import. Appends; never replaces. No model call. */
      commitImport: (preview: Partial<ResumeImportPreview>) =>
        post<{
          created: {
            roles: number;
            bullets: number;
            skills: number;
            education: number;
          };
          profile: JobProfileBundle;
        }>('/api/jobs/profile/import/commit', preview),
    },

    list: (includeDismissed = false) =>
      get<JobPosting[]>(`/api/jobs${includeDismissed ? '?dismissed=1' : ''}`),
    get: (id: string) => get<JobPosting>(`/api/jobs/${id}`),
    // `url` fetches and extracts server-side; `text` extracts from a paste.
    create: (data: {
      url?: string;
      text?: string;
      title?: string;
      company?: string;
      location?: string;
      description?: string;
    }) => post<JobPosting>('/api/jobs', data),
    update: (id: string, data: Partial<JobPosting>) =>
      patch<{ success: boolean }>(`/api/jobs/${id}`, data),
    remove: (id: string) => del<{ success: boolean }>(`/api/jobs/${id}`),

    applications: {
      list: (status?: ApplicationStatus) =>
        get<JobApplication[]>(
          `/api/jobs/applications${status ? `?status=${status}` : ''}`
        ),
      get: (id: string) =>
        get<JobApplicationDetail>(`/api/jobs/applications/${id}`),
      create: (jobId: string) =>
        post<{ id: string; existing?: boolean }>('/api/jobs/applications', {
          jobId,
        }),
      update: (
        id: string,
        data: {
          status?: ApplicationStatus;
          steer?: string;
          notes?: string;
          coverLetter?: string;
          coverLetterRequired?: boolean;
          appliedEmail?: string;
        }
      ) => patch<{ success: boolean }>(`/api/jobs/applications/${id}`, data),
      generateCoverLetter: (id: string, steer = '') =>
        post<{ coverLetter: string }>(
          `/api/jobs/applications/${id}/cover-letter`,
          { steer }
        ),
      submit: (id: string, appliedEmail?: string) =>
        post<{
          success: boolean;
          linkage: { scanned: number; linked: number };
        }>(`/api/jobs/applications/${id}/submit`, { appliedEmail }),
      remove: (id: string) =>
        del<{ success: boolean }>(`/api/jobs/applications/${id}`),
      tailor: (id: string, steer?: string) =>
        post<TailorResult>(`/api/jobs/applications/${id}/tailor`, { steer }),
      answers: (id: string, questions: FormQuestion[], steer?: string) =>
        post<{ answers: FilledAnswer[] }>(
          `/api/jobs/applications/${id}/answers`,
          { questions, steer }
        ),
      draftNote: (id: string, kind: 'follow_up' | 'thank_you', context = '') =>
        post<ApplicationNoteDraft>(`/api/jobs/applications/${id}/draft-note`, {
          kind,
          context,
        }),
      interviewPrep: {
        get: (id: string) =>
          get<{ pack: InterviewPrepPack | null }>(
            `/api/jobs/applications/${id}/interview-prep`
          ),
        generate: (id: string, notes = '') =>
          post<InterviewPrepPack>(
            `/api/jobs/applications/${id}/interview-prep`,
            { notes }
          ),
      },
      research: {
        get: (id: string) =>
          get<{ research: ApplicationResearch | null }>(
            `/api/jobs/applications/${id}/research`
          ),
        generate: (id: string, interviewer = '') =>
          post<ApplicationResearch>(`/api/jobs/applications/${id}/research`, {
            interviewer,
          }),
      },

      /** Which application, if any, is the posting at `url`. Answers with
       * null rather than a guess when nothing or several match. */
      forUrl: (url: string) =>
        get<{
          application: {
            id: string;
            status: ApplicationStatus;
            title: string;
            company: string;
          } | null;
        }>(`/api/jobs/applications/for-url?url=${encodeURIComponent(url)}`),

      recordedAnswers: {
        list: (id: string) =>
          get<{ answers: RecordedAnswer[] }>(
            `/api/jobs/applications/${id}/recorded-answers`
          ),
        /** Upserts on question text, so recording twice corrects rather than
         * duplicating and a second form page appends. */
        record: (
          id: string,
          answers: {
            question: string;
            answer: string;
            source?: RecordedAnswer['source'];
            pageUrl?: string;
          }[]
        ) =>
          post<{ written: number; answers: RecordedAnswer[] }>(
            `/api/jobs/applications/${id}/recorded-answers`,
            { answers }
          ),
        remove: (id: string, answerId: string) =>
          del<{ ok: boolean }>(
            `/api/jobs/applications/${id}/recorded-answers/${answerId}`
          ),
      },
    },

    resumes: {
      get: (id: string) => get<ResumeVersion>(`/api/jobs/resumes/${id}`),
      downloadUrl: (id: string, ext: 'pdf' | 'docx') =>
        `/api/jobs/resumes/${id}/download.${ext}`,
      /** Apply hand corrections and re-render in place. 409 once the
       * application has been sent — the version is then a record. */
      edit: (
        id: string,
        patch: { summary?: string; bullets: ResumeBulletEdit[] }
      ) => send<TailorResult>('PATCH', `/api/jobs/resumes/${id}`, patch),
    },

    linkage: {
      sweep: () =>
        post<{ scanned: number; linked: number }>('/api/jobs/linkage/sweep'),
      unlinked: () => get<UnlinkedJobEmail[]>('/api/jobs/linkage/unlinked'),
      link: (applicationId: string, emailId: string) =>
        post<{ success: boolean; statusChange: string | null }>(
          '/api/jobs/linkage/link',
          { applicationId, emailId }
        ),
      unlink: (applicationId: string, emailId: string) =>
        send<{ success: boolean }>('DELETE', '/api/jobs/linkage/link', {
          applicationId,
          emailId,
        }),
      statusProposals: () =>
        get<JobStatusProposal[]>('/api/jobs/linkage/status-proposals'),
      applyStatusProposals: (
        proposals: { applicationId: string; emailId: string }[]
      ) =>
        post<{
          applied: {
            applicationId: string;
            emailId: string;
            status: ApplicationStatus;
          }[];
        }>('/api/jobs/linkage/status-proposals/apply', { proposals }),
    },

    searches: {
      list: () => get<JobSearch[]>('/api/jobs/searches'),
      create: (data: {
        kind: JobSourceKind;
        label?: string;
        params: Record<string, unknown>;
        intervalHours?: number;
      }) => post<JobSearch>('/api/jobs/searches', data),
      update: (
        id: string,
        data: {
          label?: string;
          enabled?: boolean;
          intervalHours?: number;
          params?: Record<string, unknown>;
        }
      ) => patch<JobSearch>(`/api/jobs/searches/${id}`, data),
      remove: (id: string) => del<{ ok: boolean }>(`/api/jobs/searches/${id}`),
      run: (id: string) => post<JobSyncResult>(`/api/jobs/searches/${id}/run`),
      /** Careers page URL → the board behind it, verified. Creates nothing. */
      resolve: (url: string) =>
        post<CompanyResolution>('/api/jobs/searches/resolve', { url }),
    },
    careerWatches: {
      list: () => get<CareerPageWatch[]>('/api/jobs/career-watches'),
      create: (url: string, label = '') =>
        post<CareerPageWatch>('/api/jobs/career-watches', { url, label }),
      run: (id: string) =>
        post<{ new: number; added: number }>(
          `/api/jobs/career-watches/${id}/run`
        ),
      remove: (id: string) =>
        del<{ ok: boolean }>(`/api/jobs/career-watches/${id}`),
    },
    workdayBoards: {
      list: () => get<WorkdayBoard[]>('/api/jobs/workday-boards'),
      create: (url: string, label = '') =>
        post<WorkdayBoard>('/api/jobs/workday-boards', { url, label }),
      run: (id: string) =>
        post<{ added: number; updated: number; count: number }>(
          `/api/jobs/workday-boards/${id}/run`
        ),
      remove: (id: string) =>
        del<{ ok: boolean }>(`/api/jobs/workday-boards/${id}`),
    },

    sync: () =>
      post<{ searches: number; added: number; updated: number }>(
        '/api/jobs/sync'
      ),
    /** Re-rank the feed against the current profile. No model call. */
    rescore: () => post<{ rescored: number }>('/api/jobs/rescore'),

    feed: (limit = 100) => get<FeedJob[]>(`/api/jobs/feed?limit=${limit}`),
    /** What triage threw out. The filter discards opportunities, so it has to
     * be reviewable — see backend/jobs/triager.py. */
    filtered: (limit = 200) =>
      get<FeedJob[]>(`/api/jobs/filtered?limit=${limit}`),
    triageStatus: () => get<TriageStatus>('/api/jobs/triage/status'),
    /** Judge one posting now. Synchronous: 3-8s against a full posting. */
    triage: (jobId: string) =>
      post<{ ok: boolean; state: TriageState; job: FeedJob | null }>(
        `/api/jobs/${jobId}/triage`
      ),
    restoreTriage: (jobId: string) =>
      post<FeedJob>(`/api/jobs/${jobId}/triage/restore`),
    resetTriage: (jobId: string) =>
      post<{ ok: boolean }>(`/api/jobs/${jobId}/triage/reset`),
    runTriageGate: () =>
      post<{ scanned: number; rejected: number }>('/api/jobs/triage/gate'),

    /** Returns as soon as the row is written — the resume is built in the
     * background by backend/jobs/queue.py. */
    queue: (jobId: string, steer?: string) =>
      post<JobApplication>(`/api/jobs/${jobId}/queue`, { steer }),
    dismiss: (jobId: string, dismissed = true) =>
      post<JobPosting>(`/api/jobs/${jobId}/dismiss`, { dismissed }),
    rationale: (jobId: string) =>
      post<JobAssessment>(`/api/jobs/${jobId}/rationale`),

    queueStatus: () => get<QueueStatus>('/api/jobs/queue/status'),
    drainQueue: () =>
      post<{ submitted: string | null; reason: string }>(
        '/api/jobs/queue/drain'
      ),

    stats: () => get<JobStats>('/api/jobs/stats'),
    stale: (days = 10) =>
      get<StaleApplication[]>(`/api/jobs/outcomes/stale?days=${days}`),
    upskill: (includeResources = false) =>
      post<UpskillPlan>('/api/jobs/upskill', { includeResources }),
  },
};
