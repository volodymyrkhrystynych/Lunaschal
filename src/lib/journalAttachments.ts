/**
 * Pure helpers for journal entry attachments, kept out of the component so they
 * can be tested in the node environment (see CLAUDE.md — src/lib exists for
 * exactly this).
 */
import type { JournalAttachment } from '../hooks/api';

/** What the file picker will accept, per kind. */
export const ACCEPT_AUDIO =
  'audio/*,video/*,.m4a,.mp3,.wav,.ogg,.opus,.webm,.flac,.aac,.mp4,.mov,.m4v';
export const ACCEPT_IMAGE = 'image/*,.jpg,.jpeg,.png,.webp,.heic,.heif,.gif';

const MEDIA_EXT_RE =
  /\.(m4a|mp3|wav|ogg|oga|opus|webm|flac|aac|mp4|mov|m4v|mkv|avi|3gp|jpe?g|png|webp|heic|heif|gif)$/i;

/**
 * Which pasted/dropped files we will try to upload. Deliberately permissive —
 * the backend does the real check (backend/journal/storage.py) and it knows the
 * accepted extensions; the point here is only to ignore the text/html flavour
 * that rides along with every clipboard payload rather than to second-guess it.
 */
export function isAttachableFile(file: File): boolean {
  const type = (file.type || '').toLowerCase();
  if (
    type.startsWith('audio/') ||
    type.startsWith('video/') ||
    type.startsWith('image/')
  ) {
    return true;
  }
  // iOS hands over voice memos with an empty or generic type; fall back to the
  // extension so one isn't dropped on a technicality.
  return MEDIA_EXT_RE.test(file.name || '');
}

/**
 * A filename to send the upload under.
 *
 * A voice memo dragged out of the iOS Voice Memos app is a File whose `name` is
 * empty. `FormData.append(field, file)` then serializes `filename=""`, which the
 * backend used to reject outright — hence the explicit third argument at the
 * call site and this fallback built from the mime type.
 */
export function uploadFilenameFor(file: File): string {
  if (file.name) return file.name;
  const subtype = (file.type || '').split('/')[1]?.split(';')[0];
  const ext = subtype ? MIME_FALLBACK_EXT[subtype] || subtype : 'bin';
  return `attachment.${ext}`;
}

// Only the cases where the mime subtype is not itself a usable extension.
const MIME_FALLBACK_EXT: Record<string, string> = {
  mpeg: 'mp3',
  'x-m4a': 'm4a',
  quicktime: 'mov',
  'x-wav': 'wav',
  jpeg: 'jpg',
};

/**
 * The media files carried by a paste or drop, split into what we will upload
 * and what we refused.
 *
 * Two subtleties, both learned the hard way on iOS:
 *
 * - `.files` is preferred but is not always populated; some clipboard payloads
 *   only expose the file through `.items`, so that is read as a fallback rather
 *   than trusted first (`.items` also lists the text/plain and text/html
 *   flavours riding along, which are a filename and a link, not an attachment).
 * - The rejects are returned rather than dropped, so a paste that carried
 *   something unusable can say so instead of appearing to do nothing at all.
 */
export function filesFromTransfer(
  data:
    | (Partial<Pick<DataTransfer, 'files'>> & {
        items?: DataTransfer['items'];
      })
    | null
    | undefined
): { accepted: File[]; rejected: File[] } {
  let all = Array.from(data?.files ?? []);
  if (all.length === 0 && data?.items) {
    all = Array.from(data.items)
      .filter(item => item.kind === 'file')
      .map(item => item.getAsFile())
      .filter((f): f is File => f != null);
  }
  const accepted: File[] = [];
  const rejected: File[] = [];
  for (const file of all) {
    (isAttachableFile(file) ? accepted : rejected).push(file);
  }
  return { accepted, rejected };
}

/** Message for a paste/drop that carried files we can't attach. */
export function rejectedFilesMessage(rejected: File[]): string | null {
  if (rejected.length === 0) return null;
  const described = rejected
    .map(f => f.name || f.type || 'unknown')
    .slice(0, 3)
    .join(', ');
  return `Can't attach ${described} — audio, video and images only.`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || bytes < 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit++;
  }
  // One decimal below 10 ("1.4 MB"), none above ("12 MB") — enough precision to
  // tell a voice memo from a video, not so much that it becomes noise.
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/** Audio and video both yield speech; only an image is described. */
function isSpoken(kind: JournalAttachment['kind']): boolean {
  return kind === 'audio' || kind === 'video';
}

/**
 * The label for the button that produces AI text from an attachment. Audio and
 * video get a transcript, an image gets a caption, and all are re-runnable once
 * they've produced something (a bad transcription is worth retrying).
 */
export function transcribeLabel(a: JournalAttachment): string {
  if (a.transcriptStatus === 'running') {
    return isSpoken(a.kind) ? 'Transcribing…' : 'Describing…';
  }
  const verb = isSpoken(a.kind) ? 'Transcribe' : 'Describe';
  return a.transcript ? `Re-${verb.toLowerCase()}` : verb;
}

/** A queued job is not re-queueable; everything else is. */
export function canTranscribe(a: JournalAttachment): boolean {
  return a.transcriptStatus !== 'running';
}

/** True while any attachment has AI work in flight — drives result polling. */
export function hasRunningTranscription(
  attachments: JournalAttachment[] | undefined
): boolean {
  return (attachments ?? []).some(a => a.transcriptStatus === 'running');
}

const KIND_NOUNS: Record<JournalAttachment['kind'], [string, string]> = {
  audio: ['recording', 'recordings'],
  video: ['video', 'videos'],
  image: ['photo', 'photos'],
};

/**
 * Summary line for the collapsed attachments block: "2 photos, 1 recording".
 * Attachments are collapsed by default, so this is the only thing the reader
 * sees until they open it — it has to carry the whole content of the section.
 */
export function summarizeAttachments(
  attachments: JournalAttachment[] | undefined
): string {
  const list = attachments ?? [];
  if (list.length === 0) return 'No attachments';
  const parts: string[] = [];
  for (const kind of ['audio', 'video', 'image'] as const) {
    const n = list.filter(a => a.kind === kind).length;
    if (n) parts.push(`${n} ${KIND_NOUNS[kind][n === 1 ? 0 : 1]}`);
  }
  return parts.join(', ');
}

/**
 * A default name for a newly picked file: the filename without its extension,
 * so "voice-memo-004.m4a" becomes a label the user can accept or overwrite
 * rather than a blank box they must fill before the upload means anything.
 */
export function defaultNameFor(filename: string): string {
  const base = filename.split(/[\\/]/).pop() ?? filename;
  const dot = base.lastIndexOf('.');
  return (dot > 0 ? base.slice(0, dot) : base).trim() || base;
}
