// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { InferenceState } from '../hooks/api';
import { api } from '../hooks/api';
import { InferencePausedBanner } from './InferencePausedBanner';

function makeState(overrides: Partial<InferenceState> = {}): InferenceState {
  return {
    paused: false,
    pausedSince: null,
    model: 'qwen36',
    modelStatus: 'loaded',
    queueDepth: 0,
    lanes: {},
    ...overrides,
  };
}

function renderBanner() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <InferencePausedBanner />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('InferencePausedBanner', () => {
  it('says nothing while inference is running', async () => {
    vi.spyOn(api.settings, 'inference').mockResolvedValue(makeState());
    const { container } = renderBanner();
    await vi.waitFor(() => expect(api.settings.inference).toHaveBeenCalled());
    expect(container.querySelector('[role="status"]')).toBeNull();
  });

  it('appears while paused, and says what still works', async () => {
    vi.spyOn(api.settings, 'inference').mockResolvedValue(
      makeState({ paused: true, modelStatus: 'unloaded' })
    );
    renderBanner();

    const banner = await screen.findByRole('status');
    expect(banner.textContent).toContain('GPU inference is paused');
    // Without this the banner reads as "the app is broken" rather than "one
    // part of it is off on purpose".
    expect(banner.textContent).toContain(
      'Transcription and photo reading still work'
    );
  });

  it('counts the waiting work, in the singular when there is one', async () => {
    vi.spyOn(api.settings, 'inference').mockResolvedValue(
      makeState({ paused: true, queueDepth: 1 })
    );
    renderBanner();
    expect((await screen.findByRole('status')).textContent).toContain(
      '1 job is waiting'
    );
  });

  it('pluralises a longer queue', async () => {
    vi.spyOn(api.settings, 'inference').mockResolvedValue(
      makeState({ paused: true, queueDepth: 5 })
    );
    renderBanner();
    expect((await screen.findByRole('status')).textContent).toContain(
      '5 jobs are waiting'
    );
  });

  it('does not mention a queue that is empty', async () => {
    vi.spyOn(api.settings, 'inference').mockResolvedValue(
      makeState({ paused: true, queueDepth: 0 })
    );
    renderBanner();
    expect((await screen.findByRole('status')).textContent).not.toContain(
      'waiting'
    );
  });
});
