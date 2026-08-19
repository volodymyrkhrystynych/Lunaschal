import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';

const DEFAULT_URL = 'http://localhost:8080';
const DEFAULT_MODEL = 'qwen36';
// One fixed router alias behind the multimodal toggle below. It is *one*
// because the model is any-to-any: images and audio go through the same
// weights and the same projector, so there is nothing to choose between and
// on/off is the whole interface. It writes to both llamaVisionModel and
// llamaAudioModel — two backend columns because they gate two independent
// features, one setting because they name one download. Must match the section
// name in llama/presets.ini.
const OMNI_MODEL_ALIAS = 'gemma4-12b-omni';

export function LlamaConfigSection() {
  const [llamaUrl, setLlamaUrl] = useState(DEFAULT_URL);
  const [llamaModel, setLlamaModel] = useState(DEFAULT_MODEL);
  const [llamaVisionModel, setLlamaVisionModel] = useState('');
  const [llamaAudioModel, setLlamaAudioModel] = useState('');
  const [llamaChatVision, setLlamaChatVision] = useState(false);
  const [message, setMessage] = useState<{
    type: 'success' | 'error';
    text: string;
  } | null>(null);
  const queryClient = useQueryClient();

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });

  const { data: models } = useQuery({
    queryKey: ['settings', 'llama-models'],
    queryFn: api.settings.llamaModels,
    enabled: !!settings?.llamaUrl,
  });

  useEffect(() => {
    if (settings) {
      setLlamaUrl(settings.llamaUrl || DEFAULT_URL);
      setLlamaModel(settings.llamaModel || DEFAULT_MODEL);
      setLlamaVisionModel(settings.llamaVisionModel || '');
      setLlamaAudioModel(settings.llamaAudioModel || '');
      setLlamaChatVision(!!settings.llamaChatVision);
    }
  }, [settings]);

  const updateAI = useMutation({
    mutationFn: api.settings.updateAI,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      setMessage({ type: 'success', text: 'Settings saved successfully' });
      setTimeout(() => setMessage(null), 3000);
    },
    onError: (error: Error) =>
      setMessage({ type: 'error', text: error.message }),
  });

  const loaded = models?.find(m => m.name === llamaModel);

  return (
    <>
      {message && (
        <div
          className={`mb-4 p-3 rounded-lg ${message.type === 'success' ? 'bg-green-900/30 border border-green-600/50 text-green-200' : 'bg-red-900/30 border border-red-600/50 text-red-200'}`}
        >
          {message.text}
        </div>
      )}
      <div className="space-y-4">
        <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
          <h3 className="font-medium text-[var(--color-text)] mb-2">
            llama-server
          </h3>
          <div className="space-y-3">
            <div>
              <label className="text-sm text-[var(--color-text-muted)]">
                Server URL
              </label>
              <input
                type="text"
                value={llamaUrl}
                onChange={e => setLlamaUrl(e.target.value)}
                placeholder={DEFAULT_URL}
                className="w-full bg-transparent text-[var(--color-text)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]"
              />
            </div>
            <div>
              <label className="text-sm text-[var(--color-text-muted)]">
                Model
              </label>
              {models && models.length > 0 ? (
                <select
                  value={llamaModel}
                  onChange={e => setLlamaModel(e.target.value)}
                  className="w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]"
                >
                  {models.map(m => (
                    <option key={m.name} value={m.name}>
                      {m.name}
                      {m.status === 'loaded' ? ' — loaded' : ` — ${m.status}`}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  value={llamaModel}
                  onChange={e => setLlamaModel(e.target.value)}
                  placeholder={DEFAULT_MODEL}
                  className="w-full bg-transparent text-[var(--color-text)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]"
                />
              )}
              <p className="text-xs text-[var(--color-text-muted)] mt-1">
                A router alias — a section name in{' '}
                <code className="text-[var(--color-text)]">
                  llama/presets.ini
                </code>
                , not a file name. Switching to a model that isn't loaded costs
                a load (tens of seconds for the 35B).
                {models && models.length === 0 && (
                  <> Can't reach llama-server, so this is a free-text field.</>
                )}
              </p>
            </div>
            {loaded?.contextLength != null && (
              <p className="text-xs text-[var(--color-text-muted)]">
                Context window:{' '}
                <span className="text-[var(--color-text)]">
                  {loaded.contextLength.toLocaleString()} tokens
                </span>{' '}
                — fixed when the model loads. Change it via{' '}
                <code className="text-[var(--color-text)]">ctx-size</code> in{' '}
                <code className="text-[var(--color-text)]">
                  llama/presets.ini
                </code>
                , then restart llama-server.
              </p>
            )}
            <div>
              <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
                <input
                  type="checkbox"
                  checked={!!llamaVisionModel || !!llamaAudioModel}
                  onChange={e => {
                    const alias = e.target.checked ? OMNI_MODEL_ALIAS : '';
                    setLlamaVisionModel(alias);
                    setLlamaAudioModel(alias);
                  }}
                />
                Multimodal input (audio + images)
              </label>
              <p className="text-xs text-[var(--color-text-muted)] mt-1">
                Captions journal photo attachments, and describes non-speech
                audio in audio/video ones — the latter separate from speech
                transcription, which Parakeet/Whisper still handles. Both go
                through the{' '}
                <code className="text-[var(--color-text)]">
                  [gemma4-12b-omni]
                </code>{' '}
                preset: one any-to-any model, CPU-only, so it never competes
                with the chat model for the card. Needs a separate ~7.4 GB
                download — see the comments in{' '}
                <code className="text-[var(--color-text)]">
                  llama/presets.ini
                </code>
                .
              </p>
            </div>
            <div>
              <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
                <input
                  type="checkbox"
                  checked={llamaChatVision}
                  onChange={e => setLlamaChatVision(e.target.checked)}
                />
                Chat model reads photos
              </label>
              <p className="text-xs text-[var(--color-text-muted)] mt-1">
                Sends photos attached in Chat to the chat model itself instead
                of having the CPU-only omni model describe them first — it then
                looks at the picture with your question in hand, and can answer
                follow-ups about it. Qwen3.6 is a vision-language model, but{' '}
                <code className="text-[var(--color-text)]">[qwen36]</code> ships
                with no projector: download an{' '}
                <code className="text-[var(--color-text)]">mmproj</code>, add it
                to{' '}
                <code className="text-[var(--color-text)]">
                  llama/presets.ini
                </code>{' '}
                and confirm the preset still loads <em>before</em> ticking this.
                Leave it off and photos keep going through the omni model.
              </p>
            </div>
            <button
              onClick={() =>
                updateAI.mutate({
                  llamaUrl,
                  llamaModel,
                  llamaVisionModel,
                  llamaAudioModel,
                  llamaChatVision,
                })
              }
              disabled={updateAI.isPending}
              className="px-4 py-2 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
            >
              Save llama.cpp Settings
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
