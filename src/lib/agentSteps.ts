// Tool-step events streamed by an agent turn — the chat delegate
// (POST /api/chat/stream) and the Ideas discussion both emit this shape, and
// both persist it on the assistant message's `metadata.steps`. `stepLabel`
// turns one into human-readable progress text.

export interface AgentStep {
  tool?: string;
  arg?: string;
  ok?: boolean;
  count?: number;
  title?: string;
  error?: string;
  // Present only on the delegate's propose_* steps: what got staged for the
  // user to confirm. The card itself is rendered from the `done` event's
  // `proposals`; this is what makes the step list mention it too.
  proposal?: { kind: string; data: unknown };
}

export interface AgentSource {
  url: string;
  title?: string;
}

// The assistant message's agent metadata: `{"steps": [...], "sources": [...]}`.
// Falls back to empty arrays for any message that doesn't carry one (including
// malformed metadata), matching the fail-open shape of parseProposedTodos in
// chatSegments.ts. Messages saved by the retired web-search tab used exactly
// this shape, so old conversations keep rendering unchanged.
export function parseAgentMeta(metadata: string | null | undefined): {
  steps: AgentStep[];
  sources: AgentSource[];
} {
  if (!metadata) return { steps: [], sources: [] };
  try {
    const parsed = JSON.parse(metadata);
    return {
      steps: Array.isArray(parsed?.steps) ? parsed.steps : [],
      sources: Array.isArray(parsed?.sources) ? parsed.sources : [],
    };
  } catch {
    return { steps: [], sources: [] };
  }
}

// Full noun phrases, article included — "flashcards" is plural and reads wrong
// behind a hardcoded "a".
const PROPOSAL_LABELS: Record<string, string> = {
  propose_task: 'a to-do',
  propose_calendar_event: 'a calendar event',
  propose_calorie_log: 'a calorie entry',
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
    // Only the Ideas agent has these; the delegate's toolbox carries no wiki
    // tools. Labelling them here rather than in a second copy is the point.
    case 'wiki_list':
      return `Checked the research wiki (${step.count ?? 0} articles)`;
    case 'wiki_search':
      return `Searched the wiki for "${target}"`;
    case 'wiki_read':
      return `Read wiki note: ${target}`;
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
