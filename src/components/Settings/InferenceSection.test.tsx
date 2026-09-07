// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { InferenceState } from '../../hooks/api';
import { api } from '../../hooks/api';
import { InferenceSection } from './InferenceSection';

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

function renderSection() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <InferenceSection />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('InferenceSection', () => {
  it('offers to pause while inference is running', async () => {
    vi.spyOn(api.settings, 'inference').mockResolvedValue(makeState());
    renderSection();

    expect(await screen.findByText('GPU inference is on')).toBeTruthy();
    expect(
      screen.getByRole('button', { name: /pause for gaming/i })
    ).toBeTruthy();
  });

  it('pauses when the button is pressed', async () => {
    vi.spyOn(api.settings, 'inference').mockResolvedValue(makeState());
    const pause = vi
      .spyOn(api.settings, 'pauseInference')
      .mockResolvedValue(makeState({ paused: true, modelStatus: 'unloaded' }));
    renderSection();

    fireEvent.click(
      await screen.findByRole('button', { name: /pause for gaming/i })
    );

    await waitFor(() => expect(pause).toHaveBeenCalledTimes(1));
  });

  it('offers to resume once paused, and says what is waiting', async () => {
    vi.spyOn(api.settings, 'inference').mockResolvedValue(
      makeState({ paused: true, modelStatus: 'unloaded', queueDepth: 3 })
    );
    renderSection();

    expect(await screen.findByText('Paused for gaming')).toBeTruthy();
    expect(
      screen.getByText(
        /3 jobs waiting — they'll run when you turn this back on/
      )
    ).toBeTruthy();
    expect(
      screen.getByRole('button', { name: /resume inference/i })
    ).toBeTruthy();
  });

  it('does not claim the card is free when the model is still loaded', async () => {
    // Freeing the VRAM is the entire reason for the button, so a pause that
    // did not achieve it must not read as success.
    vi.spyOn(api.settings, 'inference').mockResolvedValue(
      makeState({ paused: true, modelStatus: 'loaded' })
    );
    renderSection();

    expect(
      await screen.findByText(/Paused, but the model is still loaded/)
    ).toBeTruthy();
  });

  it('surfaces a router that refused the unload', async () => {
    vi.spyOn(api.settings, 'inference').mockResolvedValue(
      makeState({
        paused: true,
        modelStatus: 'unloaded',
        unloadError: 'Connection refused',
      })
    );
    renderSection();

    expect(
      await screen.findByText(/The router did not answer the unload/)
    ).toBeTruthy();
  });

  it('explains what keeps working, so pausing is not a leap of faith', async () => {
    vi.spyOn(api.settings, 'inference').mockResolvedValue(makeState());
    renderSection();

    expect(await screen.findByText(/transcription keep working/)).toBeTruthy();
  });
});
