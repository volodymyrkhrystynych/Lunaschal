import type { Message } from '../hooks/api';

/**
 * Whether the Chat tab should keep asking the server what happened.
 *
 * Both things this watches for are written by a background worker rather than
 * returned to a request, so the poll is the only way either reaches the screen:
 *
 * - **A reply being generated.** `backend/delegate/runs.py` owns the assistant
 *   row and finishes it whether or not anybody is still listening, so a dropped
 *   stream or a reload leaves a `'streaming'` row that only this notices.
 * - **A voice message being transcribed.** A dictated message arrives as audio
 *   with an empty body; `chat.transcribe_recording` fills in the words a few
 *   seconds later and then starts the reply. Without this the bubble would sit
 *   there as a silent clip until something else happened to refetch.
 *
 * The second is what makes the first insufficient on its own: between the clip
 * landing and its transcript arriving there is no `'streaming'` message at all,
 * so a poll keyed only on that would stop exactly during the window the user is
 * waiting on.
 *
 * Only the newest message is checked for a running reply — an older one stuck
 * `'streaming'` is a dead run from a killed process, and polling forever on its
 * behalf would never end. Transcripts are checked across every message, because
 * several clips can be in flight at once and the newest is not necessarily the
 * one still working.
 */
export function shouldPollConversation(
  messages: Message[] | undefined
): boolean {
  if (!messages || messages.length === 0) return false;
  if (messages[messages.length - 1]?.status === 'streaming') return true;
  return messages.some(m =>
    (m.attachments ?? []).some(a => a.transcriptStatus === 'running')
  );
}

/** How often to re-ask while something is still happening. */
export const POLL_INTERVAL_MS = 1500;
