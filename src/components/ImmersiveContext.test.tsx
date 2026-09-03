// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { useState } from 'react';
import {
  ImmersiveProvider,
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
