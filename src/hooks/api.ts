// Typed API client — replaces tRPC hooks

import { uploadFilenameFor } from '../lib/journalAttachments';

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

// A to-do the briefing suggested. It lives in the briefing message's metadata
// until the user accepts it (which creates the real todo, reusing this id) or
// rejects it.
// One item on the briefing's plan for the day. Lives in the chat message's
// metadata, not the todos table — only an explicit 'accept' creates a row.
// `duplicate` is legacy: briefings written before linking existed can still
// carry it, and the link fields are absent on those.
export interface ProposedTodo {
  id: string;
  title: string;
  list: TodoList;
  priority: number;
  due: number | null;
  status: 'pending' | 'done' | 'accepted' | 'rejected' | 'duplicate';
  // The existing to-do / daily task this item restates, when there is one.
  // Crossing off a linked item completes that row too.
  linkedType?: 'todo' | 'daily' | null;
  linkedId?: string | null;
  linkedTitle?: string | null;
  resolvedAt?: number | null;
}

export interface BriefingTodoDecision {
  id: string;
  action: 'done' | 'accept' | 'reject';
  title?: string;
  priority?: number;
  due?: number | null;
  list?: TodoList;
}

// The Chat tab's two sub-tabs: the regular daily chat, and one that answers by
// actually searching the web. Each is its own conversation per chat day.
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
  /** '' | 'brave' | 'tavily' | 'searxng' — empty means the web-search chat tab
   * degrades to an explanatory failure instead of searching. */
  websearchSearchProvider: string;
  hasWebsearchSearchKey: boolean;
  websearchSearxngUrl: string;
  repoContextEnabled: boolean;
  repoContextHour: number;
  researchEnabled: boolean;
  researchSearchProvider: string;
  hasResearchSearchKey: boolean;
  researchSearxngUrl: string;
  hasGoogleOauthClient: boolean;
  emailSyncEnabled: boolean;
  emailSyncIntervalMinutes: number;
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
  'todo_completed' | 'daily_completed' | 'task_deleted';

// Which list the task came from: a todo list, or 'daily' for a daily task.
export type TaskListSource = 'todo' | 'chores' | 'archive' | 'daily';

export interface TaskEvent {
  id: string;
  kind: TaskEventKind;
  title: string;
  refId: string | null;
  taskList: TaskListSource | null;
  detail: string | null;
  createdAt: string;
}

export type TodoList = 'todo' | 'chores' | 'archive';
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

export interface EmailOauthStatus {
  connected: boolean;
  emailAddress?: string | null;
  lastSyncedAt?: string | null;
  lastSyncError?: string | null;
  syncEnabled?: boolean;
}

export type EmailCategory =
  'job_application' | 'newsletter' | 'notification' | 'personal' | 'other';

export type JobApplicationStatus =
  'sent' | 'rejection' | 'interview_next_step' | 'other_update';

export interface EmailMessage {
  id: string;
  accountId: string;
  gmailId: string;
  threadId: string | null;
  subject: string | null;
  sender: string | null;
  senderEmail: string | null;
  snippet: string | null;
  bodyText: string;
  receivedAt: string;
  category: EmailCategory | null;
  jobStatus: JobApplicationStatus | null;
  classifiedAt: string | null;
  classificationError: string | null;
}

export interface EmailStats {
  sentCount: number;
  rejectionCount: number;
  interviewNextStepCount: number;
  otherUpdateCount: number;
  nextSteps: EmailMessage[];
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

// --- Lifestyle (workouts, heatmap, body weight, selfies, calories) ---
// Chores are deliberately absent: they're todos with list='chores', so the
// Lifestyle tab uses api.tasks.todos for them rather than a parallel list.

// The four activity types, in the priority order the heatmap resolves ties by.
// Kept structurally identical to ACTIVITY_TYPES in src/lib/lifestyle.ts.
export type ActivityTypeId =
  'goodlife_brother' | 'goodlife_alone' | 'building' | 'outside';

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

// --- fetch helpers ---

// A request to an unreachable backend (e.g. the Tailscale link is down in
// network mode) otherwise hangs until the OS TCP timeout — minutes — leaving
// React Query stuck `fetching` forever instead of falling back to the persisted
// cache. A hard client-side timeout turns that into a prompt failure. Reads are
// quick, so they get a tight bound; writes may hit slow AI endpoints, so theirs
// is generous.
const READ_TIMEOUT_MS = 20_000;
const WRITE_TIMEOUT_MS = 60_000;

async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs: number
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
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
    updateAI: (
      data: Partial<
        AppSettings & {
          hfToken?: string;
          websearchSearchKey?: string;
          googleOauthClientId?: string;
          googleOauthClientSecret?: string;
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
    update: (
      id: string,
      data: { content?: string; title?: string; tags?: string[] }
    ) => patch<{ success: boolean }>(`/api/journal/${id}`, data),
    delete: (id: string) => del<{ success: boolean }>(`/api/journal/${id}`),
    polish: (id: string) =>
      post<{ success: boolean; content: string }>(`/api/journal/${id}/polish`),

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
      media?: File[];
      latitude?: number;
      longitude?: number;
    }) => {
      const form = new FormData();
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
      for (const f of data.media ?? []) form.append('media', f);
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
    today: (mode: ChatMode = 'chat') =>
      get<ConversationWithMessages | null>(`/api/chat/today?mode=${mode}`),
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
    createConversation: (data?: { title?: string; mode?: ChatMode }) =>
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
    runBriefing: () =>
      post<{
        conversationId: string;
        messageId: string;
        briefing: string;
        todosProposed: number;
      }>('/api/chat/briefing/run', {}),
    decideBriefingTodos: (
      messageId: string,
      decisions: BriefingTodoDecision[]
    ) =>
      post<{ proposedTodos: ProposedTodo[]; created: number }>(
        `/api/chat/briefing/${messageId}/todos`,
        { decisions }
      ),
    classify: (message: string) =>
      post<{ intent: string; confidence: number; [key: string]: unknown }>(
        '/api/chat/classify',
        { message }
      ),
    saveCalendar: (data: {
      conversationId: string;
      messageId?: string;
      title: string;
      description: string;
      date: string;
      time?: string;
      tags: string[];
    }) => post<{ id: string }>('/api/chat/save-calendar', data),
    saveCalories: (data: {
      messageId?: string;
      description: string;
      calories: number;
      date?: string;
    }) => post<{ id: string }>('/api/chat/save-calories', data),
    saveTask: (data: { messageId?: string; title: string; list?: string }) =>
      post<{ id: string }>('/api/chat/save-task', data),
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

  ideas: {
    list: () => get<IdeaSummary[]>('/api/ideas'),
    get: (id: string) => get<Idea>(`/api/ideas/${id}`),
    create: (data: { title?: string; rawContent?: string; tags?: string[] }) =>
      post<{ id: string }>('/api/ideas', data),
    createFromVoice: (rawContent: string) =>
      post<{ id: string }>('/api/ideas/voice', { rawContent }),
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

  newspapers: {
    getByDate: (date: string) =>
      get<FrontPage[]>(`/api/newspapers/frontpages/${date}`),
    sync: () => post<SyncResult[]>('/api/newspapers/sync'),
  },
  email: {
    oauthStatus: () => get<EmailOauthStatus>('/api/email/oauth/status'),
    disconnect: () => post<{ success: boolean }>('/api/email/oauth/disconnect'),
    syncNow: () => post<EmailSyncResult>('/api/email/sync'),
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
    create: () => post<{ id: string }>('/api/paper'),
    updateTitle: (id: string, title: string) =>
      patch<{ success: boolean }>(`/api/paper/${id}`, { title }),
    setArchiveRequested: (id: string, archiveRequested: boolean) =>
      patch<{ success: boolean }>(`/api/paper/${id}`, { archiveRequested }),
    remove: (id: string) => del<{ success: boolean }>(`/api/paper/${id}`),
    addPage: (id: string) =>
      post<{ id: string; position: number }>(`/api/paper/${id}/pages`),
    getPage: (pageId: string) =>
      get<PaperPageContent>(`/api/paper/pages/${pageId}`),
    addImage: async (
      pageId: string,
      file: Blob,
      box: { x: number; y: number; width: number; height: number },
      filename = 'pasted.png'
    ) => {
      const form = new FormData();
      form.set('image', file, filename);
      form.set('x', String(box.x));
      form.set('y', String(box.y));
      form.set('width', String(box.width));
      form.set('height', String(box.height));
      const r = await fetch(`/api/paper/pages/${pageId}/images`, {
        method: 'POST',
        credentials: 'include',
        body: form,
      });
      if (!r.ok) throw new Error((await r.json()).error ?? 'Upload failed');
      return (await r.json()) as PaperPageImage;
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
    savePage: async (
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
      const r = await fetch(`/api/paper/pages/${pageId}`, {
        method: 'PUT',
        credentials: 'include',
        body: form,
      });
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(b.error || `HTTP ${r.status}`);
      }
      return r.json() as Promise<{ success: boolean }>;
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
      upload: (image: Blob, date?: string) => {
        const form = new FormData();
        // A canvas-captured Blob has no filename; the route falls back to the
        // mime type, but a name keeps the multipart part well-formed.
        form.set('image', image, 'selfie.jpg');
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
      }) => post<CalorieLog>('/api/lifestyle/calories', data),
      delete: (id: string) =>
        del<{ success: boolean }>(`/api/lifestyle/calories/${id}`),
    },
  },
};
