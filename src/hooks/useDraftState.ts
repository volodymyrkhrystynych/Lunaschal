import { useCallback, useRef, useState, type SetStateAction } from 'react';

const prefix = 'lunaschal:draft:v1:';

function matchesShape(value: unknown, initial: unknown): boolean {
  if (initial === null)
    return (
      value === null ||
      typeof value === 'string' ||
      (typeof value === 'object' && !Array.isArray(value))
    );
  if (Array.isArray(initial)) return Array.isArray(value);
  if (typeof initial === 'object') {
    return (
      value !== null &&
      typeof value === 'object' &&
      Object.entries(initial).every(([key, field]) =>
        matchesShape((value as Record<string, unknown>)[key], field)
      )
    );
  }
  return typeof value === typeof initial;
}

function read<T>(key: string, initial: T): T {
  try {
    const raw = localStorage.getItem(prefix + key);
    if (raw !== null) {
      const value: unknown = JSON.parse(raw);
      if (matchesShape(value, initial)) return value as T;
    }
  } catch {
    /* Storage may be unavailable; editing must still work. */
  }
  return initial;
}

/** Small JSON form drafts. Persist in the setter, before React commits: a
 * killed mobile tab need not deliver an effect, timer or pagehide event.
 * Use entity-specific keys for editors. Resetting to the initial value removes
 * the stored draft. Never put files, credentials or query results here. */
export function useDraftState<T>(key: string, initial: T) {
  const [state, setState] = useState(() => ({
    key,
    value: read(key, initial),
  }));
  const current = useRef(state);
  let active = state;
  if (state.key !== key) {
    active = { key, value: read(key, initial) };
    setState(active);
  }
  current.current = active;
  const initialRef = useRef(initial);
  initialRef.current = initial;

  const setValue = useCallback(
    (action: SetStateAction<T>) => {
      // A delayed callback for an old entity must not overwrite the new entity.
      const previous =
        current.current.key === key
          ? current.current.value
          : read(key, initialRef.current);
      const value =
        typeof action === 'function'
          ? (action as (previous: T) => T)(previous)
          : action;
      try {
        if (JSON.stringify(value) === JSON.stringify(initialRef.current)) {
          localStorage.removeItem(prefix + key);
        } else {
          localStorage.setItem(prefix + key, JSON.stringify(value));
        }
      } catch {
        /* Quota/private-mode failures must not interrupt typing. */
      }
      if (current.current.key === key) {
        current.current = { key, value };
        setState(current.current);
      }
    },
    [key]
  );
  const reset = useCallback(() => setValue(initialRef.current), [setValue]);
  return [active.value, setValue, reset] as const;
}
