// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { useState } from 'react';
import {
  ImmersiveProvider,
  useBottomBarHidden,
  useHideBottomBar,
  useImmersive,
  useImmersiveView,
} from './ImmersiveContext';

function Chrome() {
  return <div>{useImmersive() ? 'hidden' : 'chrome'}</div>;
}

function Claimer({ enabled }: { enabled: boolean }) {
  useImmersiveView(enabled);
  return null;
}

function BottomBar() {
  return <div>{useBottomBarHidden() ? 'no bar' : 'bar'}</div>;
}

function BottomClaimer({ enabled }: { enabled: boolean }) {
  useHideBottomBar(enabled);
  return null;
}

function Harness({
  initial = { a: false, b: false },
}: {
  initial?: { a: boolean; b: boolean };
}) {
  const [mounted, setMounted] = useState(initial);
  return (
    <ImmersiveProvider>
      <Chrome />
      {mounted.a && <Claimer enabled />}
      {mounted.b && <Claimer enabled />}
      <button onClick={() => setMounted({ a: !mounted.a, b: mounted.b })}>
        toggle a
      </button>
      <button onClick={() => setMounted({ a: mounted.a, b: !mounted.b })}>
        toggle b
      </button>
    </ImmersiveProvider>
  );
}

describe('immersive mode', () => {
  it('shows the chrome until a view asks it not to', async () => {
    render(<Harness />);
    expect(screen.getByText('chrome')).toBeTruthy();

    await act(async () => screen.getByText('toggle a').click());
    expect(screen.getByText('hidden')).toBeTruthy();
  });

  it('puts the chrome back when the view unmounts', async () => {
    // The failure that matters here is an app left with no way to navigate,
    // so the release lives in the effect's cleanup: a view swap, an unmount or
    // a component that throws all restore it.
    render(<Harness initial={{ a: true, b: false }} />);
    expect(screen.getByText('hidden')).toBeTruthy();

    await act(async () => screen.getByText('toggle a').click());
    expect(screen.getByText('chrome')).toBeTruthy();
  });

  it('counts claims, so one view leaving does not uncover another', async () => {
    render(<Harness initial={{ a: true, b: true }} />);
    expect(screen.getByText('hidden')).toBeTruthy();

    await act(async () => screen.getByText('toggle a').click());
    expect(screen.getByText('hidden')).toBeTruthy();

    await act(async () => screen.getByText('toggle b').click());
    expect(screen.getByText('chrome')).toBeTruthy();
  });

  it('claims nothing when the view is not asking', async () => {
    // The Paper editor passes `isTouchDevice()`: a desktop with a mouse has
    // room for the chrome and expects it.
    render(
      <ImmersiveProvider>
        <Chrome />
        <Claimer enabled={false} />
      </ImmersiveProvider>
    );
    expect(screen.getByText('chrome')).toBeTruthy();
  });
});

describe('the bottom-bar-only claim', () => {
  it('takes the bar without taking the sidebar', async () => {
    // The Study desk's paper pane: half the screen is still a PDF or a video
    // being read, and stripping navigation off that half to give an A4 page
    // 5% more height is a bad trade.
    render(
      <ImmersiveProvider>
        <Chrome />
        <BottomBar />
        <BottomClaimer enabled />
      </ImmersiveProvider>
    );

    expect(screen.getByText('chrome')).toBeTruthy();
    expect(screen.getByText('no bar')).toBeTruthy();
  });

  it('is implied by the full claim, so the shell reads one flag per piece', () => {
    render(
      <ImmersiveProvider>
        <BottomBar />
        <Claimer enabled />
      </ImmersiveProvider>
    );
    expect(screen.getByText('no bar')).toBeTruthy();
  });

  it('is counted and released on its own', async () => {
    function Harness2() {
      const [on, setOn] = useState(true);
      return (
        <ImmersiveProvider>
          <BottomBar />
          {on && <BottomClaimer enabled />}
          <button onClick={() => setOn(false)}>release</button>
        </ImmersiveProvider>
      );
    }
    render(<Harness2 />);
    expect(screen.getByText('no bar')).toBeTruthy();

    await act(async () => screen.getByText('release').click());
    expect(screen.getByText('bar')).toBeTruthy();
  });
});
