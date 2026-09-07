/**
 * Pure helpers for the staged-clip strip, kept out of the component so they can
 * be tested in the node environment (see CLAUDE.md — src/lib exists for exactly
 * this).
 */
import type { RecorderStatus } from '../hooks/useRecorder';

/** How long a finished clip ran, in milliseconds. */
export function clipLengthMs(rec: {
  startedAt: number;
  endedAt: number | null;
}): number {
  if (rec.endedAt == null) return 0;
  return Math.max(0, rec.endedAt - rec.startedAt);
}

/**
 * `m:ss`, minutes uncapped. A clip is read as "how much of my thought is in
 * here", so 74 minutes is 74:00 rather than 1:14:00 — the hour boundary is not
 * a thing anyone is looking for in a voice memo.
 */
export function formatClipLength(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

/**
 * The chip's text: which clip this is, and how long it is.
 *
 * Numbered from 1 and in record order, because the transcripts arrive in that
 * order too — the chip strip is the only thing on screen that says what order
 * the finished entry will read in.
 */
export function clipLabel(
  index: number,
  rec: { startedAt: number; endedAt: number | null }
): string {
  return `Clip ${index + 1} · ${formatClipLength(clipLengthMs(rec))}`;
}

/**
 * The record button's text.
 *
 * There is deliberately no 'Transcribing…' state any more: stopping a clip
 * stages it and nothing else, so the button is immediately ready for the next
 * one. 'Saving…' is the brief window where the audio is being closed out into
 * IndexedDB — it is not the upload, which happens after the composer is sent.
 */
export function clipButtonLabel(
  status: RecorderStatus,
  starting = false
): string {
  if (status === 'recording') return '■ Stop';
  if (status === 'saving' || status === 'transcribing') return 'Saving…';
  // getUserMedia's permission prompt and the first IndexedDB write both happen
  // before the recorder can report 'recording'. Without saying so, the
  // browser's microphone indicator comes on while the button still looks
  // untouched — and the second tap that provokes is silently swallowed by the
  // recorder's duplicate-start guard.
  if (starting) return 'Starting…';
  return '● Record';
}

/** Summary for a composer that has clips staged: "2 clips · 1:00". */
export function clipsSummary(
  clips: Array<{ startedAt: number; endedAt: number | null }>
): string {
  if (clips.length === 0) return '';
  const total = clips.reduce((sum, c) => sum + clipLengthMs(c), 0);
  const noun = clips.length === 1 ? 'clip' : 'clips';
  return `${clips.length} ${noun} · ${formatClipLength(total)}`;
}
