/** Copy and tone for the GPU inference switch (Settings, and the app banner).
 *
 * Pure and node-testable, in the same spirit as src/lib/backup.ts: the
 * component renders what these return, so the wording can be pinned by a test
 * without a DOM.
 */
import type { InferenceState } from '../hooks/api';

export type Tone = 'ok' | 'paused' | 'warn';

export interface Headline {
  tone: Tone;
  title: string;
  detail: string;
}

/** How long the pause has been on, in words. Deliberately coarse — this is a
 *  "have I left it off since Tuesday?" reading, not a stopwatch. */
export function pausedFor(since: number | null, now = Date.now()): string {
  if (!since) return '';
  const seconds = Math.max(0, Math.floor(now / 1000) - since);
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours === 1 ? '1 hour' : `${hours} hours`;
  const days = Math.floor(hours / 24);
  return days === 1 ? '1 day' : `${days} days`;
}

/** The queued-work sentence. Says what will happen, not just a number — a bare
 *  count reads like a backlog rather than a promise. */
export function queueSentence(state: InferenceState | undefined): string {
  const n = state?.queueDepth ?? 0;
  if (n === 0) {
    return state?.paused
      ? 'Nothing waiting.'
      : 'Nothing waiting — background work is up to date.';
  }
  const job = n === 1 ? '1 job' : `${n} jobs`;
  return state?.paused
    ? `${job} waiting — they'll run when you turn this back on.`
    : `${job} in the queue.`;
}

export function headline(state: InferenceState | undefined): Headline {
  if (!state) {
    return { tone: 'ok', title: 'GPU inference', detail: 'Checking…' };
  }
  if (!state.paused) {
    return {
      tone: 'ok',
      title: 'GPU inference is on',
      detail:
        state.modelStatus === 'loaded'
          ? `${state.model} is loaded and holding VRAM.`
          : `${state.model} loads on the next request.`,
    };
  }
  // Paused but the model is still resident: the flag took, the unload did not.
  // Worth saying plainly — the card is not actually free yet, and the user
  // pressed this button to free the card.
  if (state.modelStatus === 'loaded' || state.modelStatus === 'loading') {
    return {
      tone: 'warn',
      title: 'Paused, but the model is still loaded',
      detail:
        `Nothing new will run on ${state.model}, but llama-server has not` +
        ' released the VRAM yet. It may still be finishing a request.',
    };
  }
  const held = pausedFor(state.pausedSince);
  return {
    tone: 'paused',
    title: 'Paused for gaming',
    detail: held
      ? `${state.model} unloaded — the card has been free for ${held}.`
      : `${state.model} unloaded — the card is free.`,
  };
}

export const TONE_CLASSES: Record<Tone, string> = {
  ok: 'border-white/10',
  paused: 'border-amber-500/40 bg-amber-500/5',
  warn: 'border-red-500/40 bg-red-500/5',
};

export const TONE_DOT: Record<Tone, string> = {
  ok: 'bg-green-500',
  paused: 'bg-amber-400',
  warn: 'bg-red-500',
};
