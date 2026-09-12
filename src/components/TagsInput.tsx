import { useState } from 'react';

/** Comma-split, trimmed, empties dropped — the parse every hand-rolled
 * tag-input implementation in the app already did on its own. */
export function splitTagsInput(input: string): string[] {
  return input
    .split(',')
    .map(t => t.trim())
    .filter(Boolean);
}

function sameTags(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((t, i) => t === b[i]);
}

/**
 * A single comma-separated text input backed by a `string[]`. Covers
 * ProfileEditor's blacklist field and RecipeList's tag fields — both were a
 * plain text box that got split into an array at commit time, with no chip
 * rendering. (CuratedTagsSection's create form is a different job — one tag
 * name per submission, not a comma list — and stays its own component.)
 *
 * Kept uncontrolled internally so a single keystroke doesn't round-trip
 * through the parent's array on every character: `draft` holds the raw text,
 * and `onChange` fires with the parsed array on blur. The `value` prop is
 * resynced into `draft` only when it changes from outside (the same
 * during-render resync ProfileEditor's `Field` already used), so an
 * in-progress edit is never clobbered by, say, a query refetch.
 */
export function TagsInput({
  value,
  onChange,
  placeholder,
  className = 'w-full bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none border border-white/10 rounded p-2',
}: {
  value: string[];
  onChange: (tags: string[]) => void;
  placeholder?: string;
  className?: string;
}) {
  const [draft, setDraft] = useState(value.join(', '));
  const [lastValue, setLastValue] = useState(value);
  if (!sameTags(value, lastValue)) {
    setLastValue(value);
    setDraft(value.join(', '));
  }

  const commit = () => {
    const parsed = splitTagsInput(draft);
    if (!sameTags(parsed, value)) onChange(parsed);
  };

  return (
    <input
      type="text"
      value={draft}
      onChange={e => setDraft(e.target.value)}
      onBlur={commit}
      placeholder={placeholder}
      className={className}
    />
  );
}
