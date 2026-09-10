// Study desk shapes and the pure bits of its logic, kept out of the components
// so they test in the node environment (the convention src/lib/ exists for).

export type StudyKind = 'pdf' | 'web' | 'youtube';
/** The right half of the desk: a Notebook file, or a handwriting paper. */
export type NoteMode = 'note' | 'paper';
export type StudyImportStatus = 'importing' | 'ready' | 'error';

export interface StudySource {
  id: string;
  title: string;
  kind: StudyKind;
  sourceUrl: string | null;
  contentType: string | null;
  sizeBytes: number;
  durationSeconds: number | null;
  /** The Notebook file this source's notes live in, or null until one is bound. */
  notePath: string | null;
  importStatus: StudyImportStatus;
  importError: string | null;
  lastOpenedAt: string | null;
  /**
   * Where the reader left off. What the number means is decided by `kind`: a
   * page for a PDF, seconds for a video, and null forever for an article — a
   * sandboxed iframe's scroll offset cannot be read from outside it.
   */
  position: number | null;
  /**
   * The handwriting paper bound to this source, or null until one is made. An
   * ordinary `papers` row — Study borrows a whole paper rather than modelling
   * pages again, so it is listed in the Paper tab like any other and page
   * creation comes with it.
   */
  paperId: string | null;
  /** Which half of the desk the right pane last showed for this source. */
  noteMode: NoteMode;
  /**
   * Flagged to move into the Journal, but still here: the move happens at the
   * next 4am, exactly as a paper's does, and the flag can be toggled back off
   * until then. A source that has already moved is not in this list at all.
   */
  pendingArchive: boolean;
  /**
   * Whether this source's bytes are reachable right now. Only ever false for a
   * video: those live on the external archive drive and nowhere else, so an
   * unplugged drive leaves the row listed and the file gone.
   */
  fileAvailable?: boolean;
  fileUnavailableReason?: string;
  createdAt: string;
  updatedAt: string;
  importProgress?: StudyImportProgress;
}

/**
 * A filed study source as the Journal feed sees it: one sitting, one card.
 *
 * The media, the pages of the paper it was written on and the text of its
 * Notebook note together -- an article read and the page of notes taken beside
 * it are not two events in the day's record, which is also why the bound paper
 * gets no card of its own.
 */
export interface JournalStudySource {
  id: string;
  title: string;
  kind: StudyKind;
  sourceUrl: string | null;
  durationSeconds: number | null;
  /** The 4am day it was filed under -- the flag's day, not the last edit's. */
  journalDate: string;
  /**
   * Where it sits inside that day: the last time it was worked on, clamped
   * into the filed day. The feed sorts on this.
   */
  archivedAt: string;
  fileUrl: string;
  fileAvailable?: boolean;
  fileUnavailableReason?: string;
  /** The bound paper's page snapshots, empty when no paper was ever made. */
  pages: { id: string; imageUrl: string | null }[];
  notePath: string | null;
  note: string | null;
  noteTruncated: boolean;
}

export interface StudyImportProgress {
  phase?: string;
  error?: string | null;
  done: boolean;
}

/** What the left pane should mount for a source. */
export type ViewerKind =
  'pdf' | 'video' | 'article' | 'importing' | 'error' | 'offline';

export function viewerKindFor(source: StudySource): ViewerKind {
  if (source.importStatus === 'error') return 'error';
  if (source.importStatus === 'importing') return 'importing';
  // Ordered before the kind checks on purpose: a ready video whose drive is
  // unplugged has nothing to render, and a <video> pointed at a 404 shows an
  // empty black box with no explanation in it.
  if (source.fileAvailable === false) return 'offline';
  if (source.kind === 'pdf') return 'pdf';
  if (source.kind === 'youtube') return 'video';
  return 'article';
}

/** Why a source cannot be opened right now, for the viewer to print. */
export const DRIVE_OFFLINE_MESSAGE = 'The archive drive is not connected.';

export function offlineReason(source: StudySource): string {
  return source.fileUnavailableReason || DRIVE_OFFLINE_MESSAGE;
}

/**
 * Whether a PATCH response really is the source it was asked about, and so is
 * safe to seed the query cache with in place of it.
 *
 * The desk and both panes answer an update by writing the row that comes back
 * straight into the cache, which is cheaper than a refetch and normally exact.
 * It is also the only path by which something that is *not* a source can get
 * cached — and that cache is persisted, so a bad row outlives the session that
 * wrote it and is rehydrated into the next one. Matching the id is the cheap
 * check that this is the row we asked about; anything else is dropped and the
 * cache keeps what it had.
 */
export function isSourceRowFor(
  value: unknown,
  id: string
): value is StudySource {
  return (
    !!value && typeof value === 'object' && (value as StudySource).id === id
  );
}

// Notes for the Study tab live in one folder of the notebook rather than a
// store of their own, so they are reachable from the Notebook tab, its index
// and `:find` like any other note.
export const STUDY_NOTE_DIR = 'study';

/**
 * The note path a source gets when it is first opened with no note bound.
 *
 * Non-ASCII is dropped rather than transliterated, so a title with no Latin
 * characters at all would slug to nothing — hence the id fallback, which is
 * also what keeps two same-named sources from sharing one note.
 *
 * The title is typed as a string and is `NOT NULL` in the schema, and this
 * still does not trust it: the row this is called with can come from the
 * *persisted* query cache, which is only as well-formed as whatever was
 * written into it. A slug is not worth throwing over — a titleless source
 * already has the id fallback that gives it a usable path.
 */
export function noteSlugFor(
  title: string | null | undefined,
  id: string
): string {
  const slug = (title ?? '')
    .toLowerCase()
    .replace(/['’]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60)
    .replace(/-+$/g, '');
  const suffix = id.slice(-6).toLowerCase();
  return `${STUDY_NOTE_DIR}/${slug ? `${slug}-${suffix}` : suffix}.md`;
}

/** `1:02:51` / `4:07` — a duration next to a video's title. */
export function formatDuration(seconds: number | null): string | null {
  if (seconds === null || !Number.isFinite(seconds) || seconds <= 0)
    return null;
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** `12.4 MB` — what a stored source costs on disk. */
export function formatSize(bytes: number): string | null {
  if (!Number.isFinite(bytes) || bytes <= 0) return null;
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

/**
 * How far in a source has been read — `page 214`, `47 min in`.
 *
 * Most of why a position is worth storing at all is being able to see it
 * without opening the thing. Null for an article, which stores none, and for
 * anything sitting at the very start: "page 1" is not progress.
 */
export function progressLabel(source: StudySource): string | null {
  const at = source.position;
  if (at === null || !Number.isFinite(at) || at <= 0) return null;
  if (source.kind === 'pdf') {
    const page = Math.floor(at);
    return page > 1 ? `page ${page}` : null;
  }
  if (source.kind === 'youtube') {
    const minutes = Math.floor(at / 60);
    return minutes >= 1 ? `${minutes} min in` : null;
  }
  return null;
}

/** The line under a source in the library: kind, then whatever else it knows. */
export function sourceSubtitle(source: StudySource): string {
  const bits: string[] = [
    { pdf: 'PDF', web: 'Web page', youtube: 'Video' }[source.kind],
  ];
  const duration = formatDuration(source.durationSeconds);
  if (duration) bits.push(duration);
  const progress = progressLabel(source);
  if (progress) bits.push(progress);
  const size = formatSize(source.sizeBytes);
  if (size) bits.push(size);
  if (source.notePath) bits.push(source.notePath);
  return bits.join(' · ');
}
