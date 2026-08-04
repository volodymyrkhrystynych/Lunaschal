// Tool-step events streamed by POST /api/chat/websearch/stream (and persisted
// on the assistant message's metadata.steps) as the model searches/reads the
// web. `stepLabel` turns one into human-readable progress text.

export interface WebSearchStep {
  tool?: string;
  arg?: string;
  ok?: boolean;
  count?: number;
  title?: string;
  error?: string;
}

export interface WebSearchSource {
  url: string;
  title?: string;
}

// The assistant message's metadata for a websearch-mode reply:
// `{"steps": [...], "sources": [...]}`. Falls back to empty arrays for any
// message that doesn't carry one (including malformed metadata), matching
// the fail-open shape of parseProposedTodos in chatSegments.ts.
export function parseWebSearchMeta(metadata: string | null | undefined): {
  steps: WebSearchStep[];
  sources: WebSearchSource[];
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

export function stepLabel(step: WebSearchStep): string {
  const target = step.title || step.arg || '';
  switch (step.tool) {
    case 'web_search':
      return step.ok
        ? `Searched the web for "${target}" — ${step.count ?? 0} results`
        : `Web search unavailable${step.error ? `: ${step.error}` : ''}`;
    case 'web_fetch':
      return step.ok ? `Read ${target}` : `Could not read ${target}`;
    default:
      return step.tool ? `Ran ${step.tool}` : 'Thinking';
  }
}
