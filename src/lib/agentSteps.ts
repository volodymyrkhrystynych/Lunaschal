// Tool-step events streamed by an agent turn — the chat delegate
// (POST /api/chat/stream) and the Ideas discussion both emit this shape, and
// both persist it on the assistant message's `metadata.steps`. `stepLabel`
// turns one into human-readable progress text.

import type { DelegateProposalRecord } from '@/hooks/api';

export interface AgentStep {
  tool?: string;
  arg?: string;
  ok?: boolean;
  count?: number;
  title?: string;
  error?: string;
  // Present only on the delegate's propose_* steps: what got staged for the
  // user to confirm. The card itself renders from `metadata.proposals`
  // (parseDelegateProposals below); this is what makes the step list mention
  // it too.
  proposal?: { kind: string; data: unknown };
}

export interface AgentSource {
  url: string;
  title?: string;
}

// The assistant message's agent metadata:
// `{"steps": [...], "sources": [...], "thinking": "..."}`.
// Falls back to empty arrays for any message that doesn't carry one (including
// malformed metadata), matching the fail-open shape of parseProposedTodos in
// chatSegments.ts. Messages saved by the retired web-search tab used exactly
// this shape, so old conversations keep rendering unchanged — as do all the
// messages written before `thinking` was persisted, which simply have none.
export function parseAgentMeta(metadata: string | null | undefined): {
  steps: AgentStep[];
  sources: AgentSource[];
  thinking: string;
  truncated: boolean;
} {
  const empty = { steps: [], sources: [], thinking: '', truncated: false };
  if (!metadata) return empty;
  try {
    const parsed = JSON.parse(metadata);
    return {
      steps: Array.isArray(parsed?.steps) ? parsed.steps : [],
      sources: Array.isArray(parsed?.sources) ? parsed.sources : [],
      thinking: typeof parsed?.thinking === 'string' ? parsed.thinking : '',
      // The reply stopped at the output ceiling — which is how a turn ends up
      // with reasoning and no answer at all.
      truncated: parsed?.truncated === true,
    };
  } catch {
    return empty;
  }
}

// Delegate confirm cards persisted on `metadata.proposals` (backend/delegate/
// runs.py stamps each with an id + 'pending' status when the run finishes).
// Fails open the same way parseAgentMeta does — a message with none, or with
// malformed metadata, just renders no cards rather than throwing.
export function parseDelegateProposals(
  metadata: string | null | undefined
): DelegateProposalRecord[] {
  if (!metadata) return [];
  try {
    const parsed = JSON.parse(metadata);
    return Array.isArray(parsed?.proposals) ? parsed.proposals : [];
  } catch {
    return [];
  }
}

// Full noun phrases, article included — "flashcards" is plural and reads wrong
// behind a hardcoded "a".
const PROPOSAL_LABELS: Record<string, string> = {
  propose_task: 'a to-do',
  propose_calendar_event: 'a calendar event',
  propose_calorie_log: 'a calorie entry',
  propose_food_log: 'a food entry',
  propose_note_to_self: 'a note to self',
  propose_flashcards: 'flashcards',
};

export function stepLabel(step: AgentStep): string {
  const target = step.title || step.arg || '';
  switch (step.tool) {
    case 'web_search':
      return step.ok
        ? `Searched the web for "${target}" — ${step.count ?? 0} results`
        : `Web search unavailable${step.error ? `: ${step.error}` : ''}`;
    case 'web_fetch':
      return step.ok ? `Read ${target}` : `Could not read ${target}`;
    case 'deep_research':
      return step.ok
        ? `Deep-researched "${target}" — ${step.count ?? 0} source${step.count === 1 ? '' : 's'}`
        : `Deep research failed${step.error ? `: ${step.error}` : ''}`;
    // Only the Ideas agent has these; the delegate's toolbox carries no wiki
    // tools. Labelling them here rather than in a second copy is the point.
    case 'wiki_list':
      return `Checked the research wiki (${step.count ?? 0} articles)`;
    case 'wiki_search':
      return `Searched the wiki for "${target}"`;
    case 'wiki_read':
      return `Read wiki note: ${target}`;
    // Not a proposal — ask_user stages nothing, which is the whole point of
    // it, so it must not read as though a card is waiting somewhere.
    case 'ask_user':
      return target
        ? `Asked for clarification about ${target}`
        : 'Asked for clarification';
    // Also not proposals: these two write immediately, with no card. Saying
    // "Staged" would be the same lie in the other direction — the user needs to
    // know something was written so they can go and unwrite it.
    case 'remember':
      return step.ok
        ? `Remembered: ${target}`
        : `Didn't remember that${step.error ? ` — ${step.error}` : ''}`;
    case 'revise_memory':
      return step.ok
        ? `Updating what's remembered: ${target}`
        : `Couldn't update what's remembered${step.error ? ` — ${step.error}` : ''}`;
    default: {
      const kind = step.tool ? PROPOSAL_LABELS[step.tool] : undefined;
      if (kind) {
        // "Staged", never "saved" — nothing is written until the user clicks
        // the confirm card, and a step list claiming otherwise is a lie they
        // only catch by going to look.
        return step.ok
          ? `Staged ${kind}${target ? `: ${target}` : ''}`
          : `Could not stage ${kind}${step.error ? ` — ${step.error}` : ''}`;
      }
      return step.tool ? `Ran ${step.tool}` : 'Thinking';
    }
  }
}
