// What to show after speaking at an event on the day timeline.
//
// The mic button applies its edit with no confirm step, which is what makes it
// quick and also what makes it opaque: a retimed event slides somewhere else
// on a scrolling timeline, a renamed one changes a line of truncated text, and
// a changed description is not drawn at all. So the button says what it did.
// This is the labelling half, kept pure so it can be tested without jsdom —
// the same reason src/lib/agentSteps.ts exists.

import type { VoiceEditResult } from '@/hooks/api';

type AppliedField = VoiceEditResult['applied'][number];

/** Field order in the label, and the word for each. Fixed rather than taken
 * from the server's array so two edits touching the same fields always read
 * the same way round. */
const FIELD_LABELS: [AppliedField, string][] = [
  ['title', 'name'],
  ['time', 'time'],
  ['description', 'description'],
  ['tags', 'tags'],
];

/** How long the confirmation stays up. Long enough to read three words while
 * looking at the event you just spoke at, short enough not to sit on top of
 * the next one. */
export const VOICE_EDIT_NOTICE_MS = 4000;

/**
 * A short summary of what a spoken sentence changed, or null when it changed
 * nothing and the caller should say so some other way.
 *
 * `endTime` never appears on its own line: it moves with `time` whenever a
 * start is spoken without a length (see backend/calendar_voice.py's
 * shift_end_time), and "time and end time" would report one change as two.
 */
export function voiceEditLabel(applied: readonly string[]): string | null {
  const changed = new Set(applied);
  if (changed.has('endTime')) changed.add('time');
  const words = FIELD_LABELS.filter(([field]) => changed.has(field)).map(
    ([, word]) => word
  );
  if (words.length === 0) return null;
  return `Updated ${words.join(', ')}`;
}
