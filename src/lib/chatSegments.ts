import type { Message, ProposedTodo } from '../hooks/api';

// The "New chat" button persists a break marker: a system message whose
// metadata is {"break": true}. Break markers divide a day's chat into segments;
// the browser sends only the current (last) segment. The backend may prepend a
// compact handoff for a New Chat boundary; a Clean slate marker suppresses it.
// Either way, the raw visible/saved history is not discarded.

export function isBreak(message: Message): boolean {
  if (message.role !== 'system' || !message.metadata) return false;
  try {
    return JSON.parse(message.metadata)?.break === true;
  } catch {
    return false;
  }
}

// The briefing's plan for the day rides in its message metadata as
// {"briefing": true, "proposedTodos": [...]}. Returns [] for any message that
// doesn't carry one — including malformed metadata, which must never take the
// whole chat down mid-render.
export function parseProposedTodos(
  metadata: string | null | undefined
): ProposedTodo[] {
  if (!metadata) return [];
  try {
    const items = JSON.parse(metadata)?.proposedTodos;
    return Array.isArray(items) ? items : [];
  } catch {
    return [];
  }
}

// Split messages into segments at each break marker. The markers themselves are
// dropped. Always returns at least one (possibly empty) segment.
export function splitSegments(messages: Message[]): Message[][] {
  const segments: Message[][] = [[]];
  for (const m of messages) {
    if (isBreak(m)) {
      segments.push([]);
    } else {
      segments[segments.length - 1].push(m);
    }
  }
  return segments;
}

// The messages sent to the model: everything after the last break marker.
export function contextMessages(messages: Message[]): Message[] {
  const segments = splitSegments(messages);
  return segments[segments.length - 1];
}
