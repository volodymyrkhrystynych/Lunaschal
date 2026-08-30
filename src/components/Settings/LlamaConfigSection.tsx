import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type LlamaModel } from '../../hooks/api';

const DEFAULT_URL = 'http://localhost:8080';
const DEFAULT_MODEL = 'qwen36';

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
            <ModalitySelect
              label="Vision model (photo captions)"
              testId="llama-vision-model"
              value={llamaVisionModel}
              onChange={setLlamaVisionModel}
              models={models}
              modality="image"
              help={
                <>
                  Captions journal photo attachments, and the caption is what
                  the entry's title is generated from.{' '}
                  <code className="text-[var(--color-text)]">[qwen36]</code>{' '}
                  carries its own projector with{' '}
                  <code className="text-[var(--color-text)]">
                    mmproj-offload = false
                  </code>
                  , so it reads images without taking any VRAM from the chat
                  model it already is.
                </>
              }
            />
            <ModalitySelect
              label="Audio description model"
              testId="llama-audio-model"
              value={llamaAudioModel}
              onChange={setLlamaAudioModel}
              models={models}
              modality="audio"
              help={
                <>
                  Describes non-speech content in audio and video attachments —
                  separate from speech transcription, which Parakeet/Whisper
                  still handles. Only{' '}
                  <code className="text-[var(--color-text)]">
                    [gemma4-12b-omni]
                  </code>{' '}
                  reports an audio encoder, and it is a separate ~7.4 GB
                  download that needs room beside the chat model.
                </>
              }
            />
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
                of having the vision model describe them first — it then looks
                at the picture with your question in hand, and can answer
                follow-ups about it. Safe to tick whenever the chat model above
                is listed as taking images — Qwen3.6 is a vision-language model
                and <code className="text-[var(--color-text)]">[qwen36]</code>{' '}
                now sets an{' '}
                <code className="text-[var(--color-text)]">mmproj</code>. Leave
                it off and chat photos are described first by the vision model
                above.
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

/**
 * Picks the router alias for one non-text modality, offering only the models the
 * router says can actually take it.
 *
 * This replaced a single "Multimodal input" checkbox that wrote one hardcoded
 * alias — `gemma4-12b-omni` — into both the vision and audio columns. Two things
 * were wrong with that. It could not express the situation the app is actually
 * in (the chat model reads images and the omni model reads audio, so they are
 * two different answers), and a hardcoded alias is exactly how the settings row
 * came to hold `gemma4-vision`, a preset that never existed, for months: nothing
 * validated it, and every caption 404'd at the router looking like a merely
 * unconfigured feature.
 *
 * `GET /api/settings/llama-models` has reported `inputModalities` per model all
 * along — it was fetched here, typed, and used for nothing. Offering only the
 * capable models makes a dead alias unpickable, and a stored alias the router
 * does not list is now called out rather than silently accepted.
 */
function ModalitySelect({
  label,
  testId,
  value,
  onChange,
  models,
  modality,
  help,
}: {
  label: string;
  testId: string;
  value: string;
  onChange: (value: string) => void;
  models: LlamaModel[] | undefined;
  modality: 'image' | 'audio';
  help: React.ReactNode;
}) {
  const capable = (models ?? []).filter(m =>
    m.inputModalities?.includes(modality)
  );
  // A value that is set but not on offer: a retired preset, a renamed section,
  // or llama-server being unreachable. It stays selected — silently dropping it
  // would rewrite the user's setting on a transient failure — and is labelled.
  const unknown = !!value && !capable.some(m => m.name === value);

  return (
    <div>
      <label className="text-sm text-[var(--color-text)]">{label}</label>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        data-testid={testId}
        className="w-full mt-1 bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 focus:outline-none focus:border-[var(--color-primary)]"
      >
        <option value="">Off</option>
        {capable.map(m => (
          <option key={m.name} value={m.name}>
            {m.name}
            {m.status === 'loaded' ? ' — loaded' : ` — ${m.status}`}
          </option>
        ))}
        {unknown && <option value={value}>{value} — not on the router</option>}
      </select>
      {unknown && (
        <p className="text-xs text-amber-400 mt-1">
          llama-server does not list <code>{value}</code> as taking {modality}.
          Either it is not running, or this names a preset that no longer exists
          — in which case every call using it will fail at the router.
        </p>
      )}
      {!unknown && capable.length === 0 && (
        <p className="text-xs text-[var(--color-text-muted)] mt-1">
          No model on the router reports {modality} input.
        </p>
      )}
      <p className="text-xs text-[var(--color-text-muted)] mt-1">{help}</p>
    </div>
  );
}
