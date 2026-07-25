import type { Message } from '../hooks/api';

// The "New chat" button persists a break marker: a system message whose
// metadata is {"break": true}. Break markers divide a day's chat into segments;
// the AI only ever sees the current (last) segment, so pressing the button acts
// as a true "clear" without discarding the visible/saved history.

export function isBreak(message: Message): boolean {
  if (message.role !== 'system' || !message.metadata) return false;
  try {
    return JSON.parse(message.metadata)?.break === true;
  } catch {
    return false;
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
