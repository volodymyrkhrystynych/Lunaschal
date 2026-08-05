import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';

const DEFAULT_URL = 'http://localhost:8080';
const DEFAULT_MODEL = 'gemma4';
// Fixed router aliases for the vision/audio toggles below — there is nothing
// to choose between (each names exactly one preset), so on/off is the whole
// interface. Must match backend/ai/images.py / backend/ai/audio_description.py
// and the section names in llama/presets.ini.
const VISION_MODEL_ALIAS = 'gemma4-vision';
const AUDIO_MODEL_ALIAS = 'gemma4-e4b-audio';

export function LlamaConfigSection() {
  const [llamaUrl, setLlamaUrl] = useState(DEFAULT_URL);
  const [llamaModel, setLlamaModel] = useState(DEFAULT_MODEL);
  const [llamaVisionModel, setLlamaVisionModel] = useState('');
  const [llamaAudioModel, setLlamaAudioModel] = useState('');
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
      <section className="mb-8">
        <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">
          llama.cpp Configuration
        </h2>
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
                  , not a file name. Switching to a model that isn't loaded
                  costs a load (tens of seconds for the 26B).
                  {models && models.length === 0 && (
                    <>
                      {' '}
                      Can't reach llama-server, so this is a free-text field.
                    </>
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
                    checked={!!llamaVisionModel}
                    onChange={e =>
                      setLlamaVisionModel(
                        e.target.checked ? VISION_MODEL_ALIAS : ''
                      )
                    }
                  />
                  Photo captioning
                </label>
                <p className="text-xs text-[var(--color-text-muted)] mt-1">
                  Enables AI captions for journal photo attachments, via the{' '}
                  <code className="text-[var(--color-text)]">
                    [gemma4-vision]
                  </code>{' '}
                  preset with an{' '}
                  <code className="text-[var(--color-text)]">mmproj</code>{' '}
                  projector — both chat presets set{' '}
                  <code className="text-[var(--color-text)]">
                    mmproj-auto = false
                  </code>{' '}
                  because the ~1.1 GB vision tower doesn't fit alongside the
                  26B.
                </p>
              </div>
              <div>
                <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
                  <input
                    type="checkbox"
                    checked={!!llamaAudioModel}
                    onChange={e =>
                      setLlamaAudioModel(
                        e.target.checked ? AUDIO_MODEL_ALIAS : ''
                      )
                    }
                  />
                  Audio description
                </label>
                <p className="text-xs text-[var(--color-text-muted)] mt-1">
                  Describes non-speech audio in journal audio/video attachments
                  — separate from speech transcription — via the{' '}
                  <code className="text-[var(--color-text)]">
                    [gemma4-e4b-audio]
                  </code>{' '}
                  preset, a smaller, audio-capable Gemma 4 variant; the 26B chat
                  model has no audio input at all.
                </p>
              </div>
              <button
                onClick={() =>
                  updateAI.mutate({
                    llamaUrl,
                    llamaModel,
                    llamaVisionModel,
                    llamaAudioModel,
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
      </section>
    </>
  );
}
