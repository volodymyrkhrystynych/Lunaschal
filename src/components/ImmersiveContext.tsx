import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

/**
 * Immersive mode: a view asking the app shell to get out of its way.
 *
 * There is exactly one user of this — the Paper editor on a tablet — and one
 * reason for it. Paper is drawn on with a stylus on an iPad, where the app's
 * own chrome (the ☰ header, the sidebar rail, the Transcribe/Journal/Record
 * bar along the bottom) is both wasted vertical space beside an A4 page and a
 * row of stray tap targets a resting palm can hit. Inside a page the editor's
 * own `‹ Back` is meant to be the only way out.
 *
 * A context rather than a prop because the views are rendered from a `switch`
 * in App's `renderView()`, so a prop would have to be threaded through every
 * branch of it to reach one component.
 */

interface ImmersiveValue {
  immersive: boolean;
  /** Ref-counted: two overlapping claims must not let the first one to unmount
   *  put the chrome back underneath the second. */
  claim: () => () => void;
}

const ImmersiveContext = createContext<ImmersiveValue>({
  immersive: false,
  claim: () => () => {},
});

export function ImmersiveProvider({ children }: { children: ReactNode }) {
  const [claims, setClaims] = useState(0);
  const claim = useCallback(() => {
    setClaims(n => n + 1);
    let released = false;
    return () => {
      if (released) return;
      released = true;
      setClaims(n => Math.max(0, n - 1));
    };
  }, []);
  const value = useMemo(
    () => ({ immersive: claims > 0, claim }),
    [claims, claim]
  );
  return (
    <ImmersiveContext.Provider value={value}>
      {children}
    </ImmersiveContext.Provider>
  );
}

/** Read the current state. For the shell, which decides what to render. */
export function useImmersive(): boolean {
  return useContext(ImmersiveContext).immersive;
}

/**
 * Hide the app chrome for as long as this component is mounted and `enabled`.
 *
 * The release happens in the effect's cleanup, so a view swap, an unmount or a
 * component that throws all put the chrome back — the one failure mode that
 * really matters here is an app left permanently without a way to navigate.
 */
export function useImmersiveView(enabled: boolean): void {
  const { claim } = useContext(ImmersiveContext);
  useEffect(() => {
    if (!enabled) return;
    return claim();
  }, [enabled, claim]);
}
