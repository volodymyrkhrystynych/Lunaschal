import type { StoredRecording } from '../offline/recordingStore';

/**
 * Presentation rules for journal audio still held on the device. Pure so it can
 * be tested without IndexedDB or a DOM — the store and the component around it
 * are thin on purpose.
 */

/**
 * How long a finished recording may sit unsent before it is worth mentioning.
 *
 * The happy path is a couple of hundred milliseconds, so anything past this is
 * genuinely waiting on something. Short enough that a user who locked their
 * phone mid-upload sees the recording is still there; long enough that a normal
 * save never flashes a warning at them.
 */
export const PENDING_GRACE_MS = 15_000;

/**
 * Which recordings the user should be told about. A recording that uploads on
 * the first try is never shown at all: the point is to surface the ones that
 * didn't, not to narrate the ones that did.
 */
export function visibleRecordings(
  all: StoredRecording[],
  now: number = Date.now()
): StoredRecording[] {
  return all.filter(rec => {
    if (rec.failed) return true;
    // An attempt is only recorded when one fails.
    if (rec.attempts > 0) return true;
    // Never attempted and still here: paused offline, or queued behind
    // something. Give it a moment before saying so.
    if (rec.endedAt === null) return false;
    return now - rec.endedAt > PENDING_GRACE_MS;
  });
}

/** `2:05` / `0:12` — mm:ss, or null if the recording hasn't ended. */
export function durationLabel(rec: StoredRecording): string | null {
  if (rec.endedAt === null) return null;
  const seconds = Math.max(0, Math.round((rec.endedAt - rec.startedAt) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function clockLabel(ms: number): string {
  const d = new Date(ms);
  return `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/**
 * One line describing a held recording: when it was made, how long it is, and
 * why it is still here. The "why" is the part that matters — a recording the
 * server refused reads differently from one waiting for the backend to come
 * back, and only one of them needs the user to do anything.
 */
export function pendingRecordingLabel(
  rec: StoredRecording,
  now: number = Date.now()
): string {
  const duration = durationLabel(rec);
  const head = `${clockLabel(rec.startedAt)}${duration ? ` · ${duration}` : ''}`;

  const parts = [head];
  if (rec.recovered) parts.push('ended early, saved');
  if (rec.failed) {
    parts.push(rec.lastError || "the server wouldn't take it");
  } else if (rec.attempts > 0) {
    parts.push(`retrying — ${rec.lastError || 'upload failed'}`);
  } else if (!rec.finalized) {
    parts.push('recording');
  } else {
    const waited = Math.round((now - (rec.endedAt ?? now)) / 1000);
    parts.push(waited > 60 ? 'waiting for the server' : 'waiting to upload');
  }
  return parts.join(' · ');
}
