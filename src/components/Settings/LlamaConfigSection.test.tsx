// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { LlamaConfigSection } from './LlamaConfigSection';
import { api, type AppSettings } from '../../hooks/api';

vi.mock('../../hooks/api', () => ({
  api: {
    settings: {
      get: vi.fn(),
      llamaModels: vi.fn(),
      updateAI: vi.fn(),
    },
  },
}));

function settings(over: Partial<AppSettings> = {}) {
  return {
    llamaUrl: 'http://llama.test:9999',
    llamaModel: 'qwen36',
    llamaVisionModel: '',
    llamaAudioModel: '',
    ...over,
  } as AppSettings;
}

function renderSection() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <LlamaConfigSection />
    </QueryClientProvider>
  );
}

const toggle = () =>
  screen.getByLabelText(/multimodal input/i) as HTMLInputElement;
const save = () => screen.getByRole('button', { name: /save llama\.cpp/i });

/** The saved payload. Read off `mock.calls` rather than asserted with
 *  `toHaveBeenCalledWith`, because React Query hands `mutationFn` a second
 *  context argument and an exact-arity matcher never matches. */
async function saved() {
  await waitFor(() => expect(api.settings.updateAI).toHaveBeenCalled());
  return vi.mocked(api.settings.updateAI).mock.calls[0][0];
}

/** The form renders before the settings query resolves and a `useEffect` seeds
 *  it from the response, so anything clicked in between is silently overwritten.
 *  Waiting on the toggle itself is not enough — it is unchecked both before and
 *  after a seed that leaves it off. The fixture URL is deliberately not the
 *  component's default, so its arrival is observable. */
async function seeded() {
  await waitFor(() =>
    expect(screen.getByDisplayValue('http://llama.test:9999')).toBeTruthy()
  );
}

describe('LlamaConfigSection multimodal toggle', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.settings.get).mockResolvedValue(settings());
    vi.mocked(api.settings.llamaModels).mockResolvedValue([]);
    vi.mocked(api.settings.updateAI).mockResolvedValue(undefined as never);
  });

  it('writes the one omni alias to both the vision and audio settings', async () => {
    // The whole point of collapsing the two checkboxes: images and audio go to
    // the same any-to-any model, so a user who ticks one has already chosen the
    // other. Two columns, one decision.
    renderSection();
    await seeded();

    fireEvent.click(toggle());
    fireEvent.click(save());

    expect(await saved()).toMatchObject({
      llamaVisionModel: 'gemma4-12b-omni',
      llamaAudioModel: 'gemma4-12b-omni',
    });
  });

  it('clears both settings when unticked', async () => {
    vi.mocked(api.settings.get).mockResolvedValue(
      settings({
        llamaVisionModel: 'gemma4-12b-omni',
        llamaAudioModel: 'gemma4-12b-omni',
      })
    );
    renderSection();
    await seeded();
    expect(toggle().checked).toBe(true);

    fireEvent.click(toggle());
    fireEvent.click(save());

    expect(await saved()).toMatchObject({
      llamaVisionModel: '',
      llamaAudioModel: '',
    });
  });

  it('reads a half-configured row as on, and one click repairs it', async () => {
    // Reachable state: the two columns predate the single toggle, so an older DB
    // can hold one alias and not the other. Reading that as "off" would hide a
    // feature that is half working; reading it as "on" means one click turns it
    // off and the next turns both back on together.
    vi.mocked(api.settings.get).mockResolvedValue(
      settings({ llamaAudioModel: 'gemma4-12b-omni' })
    );
    renderSection();
    await seeded();
    expect(toggle().checked).toBe(true);

    fireEvent.click(toggle());
    fireEvent.click(save());

    expect(await saved()).toMatchObject({
      llamaVisionModel: '',
      llamaAudioModel: '',
    });
  });

  it('offers the model as free text when llama-server is unreachable', async () => {
    // Unchanged behaviour, pinned because the swap moved DEFAULT_MODEL: the
    // placeholder is the only place the new default alias is visible to someone
    // whose router is down.
    renderSection();

    await waitFor(() =>
      expect(screen.getByPlaceholderText('qwen36')).toBeTruthy()
    );
  });
});

describe('chat model reads photos', () => {
  const chatVision = () =>
    screen.getByLabelText(/chat model reads photos/i) as HTMLInputElement;

  it('is off by default and saves when ticked', async () => {
    // Off is the safe default: `[qwen36]` ships with no projector, so sending
    // images to it would look like the model hallucinating rather than like a
    // missing mmproj.
    renderSection();
    await seeded();
    expect(chatVision().checked).toBe(false);

    fireEvent.click(chatVision());
    fireEvent.click(save());
    expect(await saved()).toMatchObject({ llamaChatVision: true });
  });

  it('is independent of the omni model checkbox', async () => {
    // They gate different things: the omni model still serves journal audio
    // description, which Qwen3.6 cannot do at all.
    renderSection();
    await seeded();

    fireEvent.click(chatVision());
    expect(toggle().checked).toBe(false);
    expect(chatVision().checked).toBe(true);
  });
});
