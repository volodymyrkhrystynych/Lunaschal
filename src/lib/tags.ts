// Split a comma-separated tag input into trimmed, non-empty segments.
// Normalization (lowercasing, deduping) is owned by the backend, which
// re-normalizes every create/update payload — keeping one source of truth
// for what makes two tags "the same".
export function parseTagsInput(input: string): string[] {
  return input
    .split(',')
    .map(t => t.trim())
    .filter(Boolean);
}
