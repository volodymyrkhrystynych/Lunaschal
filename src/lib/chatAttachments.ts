/**
 * Pure helpers for photos attached to a chat message, kept out of the component
 * so they can be tested in the node environment (see CLAUDE.md — src/lib exists
 * for exactly this).
 *
 * Images only, unlike the journal's attachments: a chat photo exists to be read
 * by the vision model and turned into text for the conversation, and nothing in
 * this feature wants the ffmpeg audio-extraction path a video would need.
 */
import type { ChatAttachment } from '../hooks/api';

/** What the picker will accept. HEIC is included and transcoded server-side. */
export const ACCEPT_PHOTO = 'image/*,.jpg,.jpeg,.png,.webp,.heic,.heif,.gif';

const IMAGE_EXT_RE = /\.(jpe?g|png|webp|heic|heif|gif)$/i;

/**
 * Which pasted/dropped files we will try to attach. Deliberately permissive
 * about type — the backend does the real check (backend/chat/storage.py) — but
 * strict about it being an image, because a paste that quietly attached a video
 * would produce a photo the model can never read.
 */
export function isAttachablePhoto(file: File): boolean {
  if ((file.type || '').toLowerCase().startsWith('image/')) return true;
  // iOS sometimes hands over a photo with an empty or generic type; fall back to
  // the extension rather than dropping it on a technicality.
  return !file.type && IMAGE_EXT_RE.test(file.name || '');
}

/** The photos carried by a paste or drop, split into what we'll send and what we won't. */
export function photosFromTransfer(
  data:
    | (Partial<Pick<DataTransfer, 'files'>> & { items?: DataTransfer['items'] })
    | null
    | undefined
): { accepted: File[]; rejected: File[] } {
  let all = Array.from(data?.files ?? []);
  // `.files` is not always populated — some clipboard payloads only expose the
  // file through `.items`, which also lists the text/plain and text/html
  // flavours riding along. Hence the `kind === 'file'` filter.
  if (all.length === 0 && data?.items) {
    all = Array.from(data.items)
      .filter(item => item.kind === 'file')
      .map(item => item.getAsFile())
      .filter((f): f is File => f != null);
  }
  const accepted: File[] = [];
  const rejected: File[] = [];
  for (const file of all) {
    (isAttachablePhoto(file) ? accepted : rejected).push(file);
  }
  return { accepted, rejected };
}

/** Message for a paste/drop that carried files we can't attach, or null. */
export function rejectedPhotosMessage(rejected: File[]): string | null {
  if (rejected.length === 0) return null;
  const described = rejected
    .map(f => f.name || f.type || 'unknown')
    .slice(0, 3)
    .join(', ');
  return `Can't attach ${described} — photos only.`;
}

/**
 * Whether any attached photo is still being read.
 *
 * Worth surfacing rather than hiding: the reading happens on a CPU-only model
 * and takes real seconds, and a message sent before it lands reaches the chat
 * model with the photo described as "not finished being read yet".
 */
export function isReadingPhotos(attachments: ChatAttachment[]): boolean {
  return attachments.some(a => a.descriptionStatus === 'running');
}

/**
 * The one-line status under the thumbnail strip, or null when there is nothing
 * worth saying.
 *
 * Three cases, and which one applies depends on how photos reach the model:
 *
 * - `chatVision` — the chat model reads the picture itself, so there is no
 *   pre-read phase and nothing to report. Silence is correct.
 * - neither configured — no model can read the photo at all. Say so, because a
 *   spinner that never resolves is the failure the Ideas sketch caption warns
 *   about.
 * - otherwise the omni model is pre-reading, which takes real seconds on CPU.
 */
export function photoStatusMessage(
  attachments: ChatAttachment[],
  visionConfigured: boolean,
  chatVision = false
): string | null {
  if (attachments.length === 0) return null;
  if (chatVision) return null;
  if (!visionConfigured) {
    return 'Photos are attached, but nothing is set up to read them — turn on Multimodal input or "Chat model reads photos" in Settings → llama.cpp.';
  }
  if (isReadingPhotos(attachments)) {
    const n = attachments.filter(a => a.descriptionStatus === 'running').length;
    return n === 1 ? 'Reading the photo…' : `Reading ${n} photos…`;
  }
  const failed = attachments.filter(
    a => a.descriptionStatus === 'error'
  ).length;
  if (failed > 0) {
    return failed === 1
      ? "One photo couldn't be read — it'll be attached, but not described."
      : `${failed} photos couldn't be read — they'll be attached, but not described.`;
  }
  return null;
}
