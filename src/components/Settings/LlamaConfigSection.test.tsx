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

const visionSelect = () =>
  screen.getByTestId('llama-vision-model') as HTMLSelectElement;
const audioSelect = () =>
  screen.getByTestId('llama-audio-model') as HTMLSelectElement;
const optionNames = (el: HTMLSelectElement) =>
  Array.from(el.options).map(o => o.value);
const save = () => screen.getByRole('button', { name: /save llama\.cpp/i });

/** What `GET /api/settings/llama-models` reports for this box today. */
const ROUTER_MODELS = [
  { name: 'embed', status: 'loaded', inputModalities: ['text'] },
  {
    name: 'gemma4-12b-omni',
    status: 'unloaded',
    inputModalities: ['text', 'image', 'audio'],
  },
  {
    name: 'qwen36',
    status: 'loaded',
    inputModalities: ['text', 'image'],
    contextLength: 190208,
  },
];

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

describe('LlamaConfigSection multimodal models', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.settings.get).mockResolvedValue(settings());
    vi.mocked(api.settings.llamaModels).mockResolvedValue(
      ROUTER_MODELS as never
    );
    vi.mocked(api.settings.updateAI).mockResolvedValue(undefined as never);
  });

  it('offers only the models the router says take images', async () => {
    // This replaced a checkbox that wrote one hardcoded alias into both
    // columns. `inputModalities` was already being fetched here and used for
    // nothing; using it is what makes a dead alias unpickable.
    renderSection();
    await seeded();

    expect(optionNames(visionSelect())).toEqual([
      '',
      'gemma4-12b-omni',
      'qwen36',
    ]);
  });

  it('offers only the models that take audio, which is not the chat model', async () => {
    // Gemma's projector is the only one reporting has_audio_encoder. Vision and
    // audio are two different answers now, which the single checkbox could not
    // express.
    renderSection();
    await seeded();

    expect(optionNames(audioSelect())).toEqual(['', 'gemma4-12b-omni']);
  });

  it('saves the two independently', async () => {
    renderSection();
    await seeded();

    fireEvent.change(visionSelect(), { target: { value: 'qwen36' } });
    fireEvent.change(audioSelect(), { target: { value: 'gemma4-12b-omni' } });
    fireEvent.click(save());

    expect(await saved()).toMatchObject({
      llamaVisionModel: 'qwen36',
      llamaAudioModel: 'gemma4-12b-omni',
    });
  });

  it('turns captioning off with the explicit Off option', async () => {
    vi.mocked(api.settings.get).mockResolvedValue(
      settings({ llamaVisionModel: 'qwen36' })
    );
    renderSection();
    await seeded();
    await waitFor(() => expect(visionSelect().value).toBe('qwen36'));

    fireEvent.change(visionSelect(), { target: { value: '' } });
    fireEvent.click(save());

    expect(await saved()).toMatchObject({ llamaVisionModel: '' });
  });

  it('flags a stored alias the router does not know, and keeps it selected', async () => {
    // The `gemma4-vision` failure, made visible. It named a preset that never
    // existed in llama/presets.ini, nothing validated it, and every caption
    // 404'd looking like a merely unconfigured feature. Keeping it selected
    // matters too: silently dropping it would rewrite the setting whenever
    // llama-server happened to be down.
    vi.mocked(api.settings.get).mockResolvedValue(
      settings({ llamaVisionModel: 'gemma4-vision' })
    );
    renderSection();
    await seeded();

    await waitFor(() => expect(visionSelect().value).toBe('gemma4-vision'));
    expect(screen.getByText(/does not list/, { exact: false })).toBeTruthy();
  });

  it('offers the model as free text when llama-server is unreachable', async () => {
    // Unchanged behaviour, pinned because the swap moved DEFAULT_MODEL: the
    // placeholder is the only place the new default alias is visible to someone
    // whose router is down.
    vi.mocked(api.settings.llamaModels).mockResolvedValue([]);
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
    // Off stays the default even though `[qwen36]` carries a projector now: an
    // upgrade should not change how chat photos are handled on its own.
    renderSection();
    await seeded();
    expect(chatVision().checked).toBe(false);

    fireEvent.click(chatVision());
    fireEvent.click(save());
    expect(await saved()).toMatchObject({ llamaChatVision: true });
  });

  it('is independent of the vision model', async () => {
    // They gate different things: `llamaVisionModel` is the one-shot captioner
    // for journal photos, this is whether the chat model gets the picture
    // itself, with the question already in hand.
    vi.mocked(api.settings.llamaModels).mockResolvedValue(
      ROUTER_MODELS as never
    );
    renderSection();
    await seeded();

    fireEvent.click(chatVision());
    expect(visionSelect().value).toBe('');
    expect(chatVision().checked).toBe(true);
  });
});
