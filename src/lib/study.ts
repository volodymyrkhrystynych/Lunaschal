// Study desk shapes and the pure bits of its logic, kept out of the components
// so they test in the node environment (the convention src/lib/ exists for).

export type StudyKind = 'pdf' | 'web' | 'youtube';
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
 */
export function noteSlugFor(title: string, id: string): string {
  const slug = title
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

/** The line under a source in the library: kind, then whatever else it knows. */
export function sourceSubtitle(source: StudySource): string {
  const bits: string[] = [
    { pdf: 'PDF', web: 'Web page', youtube: 'Video' }[source.kind],
  ];
  const duration = formatDuration(source.durationSeconds);
  if (duration) bits.push(duration);
  const size = formatSize(source.sizeBytes);
  if (size) bits.push(size);
  if (source.notePath) bits.push(source.notePath);
  return bits.join(' · ');
}
