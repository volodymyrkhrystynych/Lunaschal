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
 * There is a second, weaker claim beside it: `useHideBottomBar`, which drops
 * only the Transcribe/Journal/Record strip and leaves the sidebar and header
 * alone. The Study desk's handwriting pane asks for that one. The desk is a
 * split view, so the full claim would strip navigation away from the PDF or
 * video being read on the *left* as well — and on the screens Study runs on
 * (>=1024px) the sidebar is already a left-hand column and the header is
 * already `md:hidden`, so the bottom strip is the only chrome that actually
 * crosses under the page being written on.
 *
 * A context rather than a prop because the views are rendered from a `switch`
 * in App's `renderView()`, so a prop would have to be threaded through every
 * branch of it to reach one component.
 */

interface ImmersiveValue {
  immersive: boolean;
  bottomBarHidden: boolean;
  /** Ref-counted: two overlapping claims must not let the first one to unmount
   *  put the chrome back underneath the second. */
  claim: () => () => void;
  claimBottomBar: () => () => void;
}

const ImmersiveContext = createContext<ImmersiveValue>({
  immersive: false,
  bottomBarHidden: false,
  claim: () => () => {},
  claimBottomBar: () => () => {},
});

/** One ref-counted claim: `n > 0` while anything is holding it. */
function useClaimCount(): [boolean, () => () => void] {
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
  return [claims > 0, claim];
}

export function ImmersiveProvider({ children }: { children: ReactNode }) {
  const [immersive, claim] = useClaimCount();
  const [bottomOnly, claimBottomBar] = useClaimCount();
  const value = useMemo(
    () => ({
      immersive,
      // Strictly weaker, so the full claim implies it — the shell then only
      // has to read one flag per piece of chrome.
      bottomBarHidden: immersive || bottomOnly,
      claim,
      claimBottomBar,
    }),
    [immersive, bottomOnly, claim, claimBottomBar]
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

/** Whether the bottom Transcribe/Journal/Record strip should be rendered. */
export function useBottomBarHidden(): boolean {
  return useContext(ImmersiveContext).bottomBarHidden;
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

/**
 * Hide only the bottom bar, keeping the sidebar and header.
 *
 * For a view that is giving half its width to something page-shaped and wants
 * the height back, without taking navigation away from the other half.
 */
export function useHideBottomBar(enabled: boolean): void {
  const { claimBottomBar } = useContext(ImmersiveContext);
  useEffect(() => {
    if (!enabled) return;
    return claimBottomBar();
  }, [enabled, claimBottomBar]);
}
